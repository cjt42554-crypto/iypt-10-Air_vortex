from __future__ import annotations

import math
import sys
import tempfile
import unittest
from contextlib import ExitStack
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import numpy as np

HERE = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = HERE.parents[2]
FORMAL_SOURCE = REPOSITORY_ROOT / "02_模拟程序" / "src"
if str(FORMAL_SOURCE) not in sys.path:
    sys.path.insert(0, str(FORMAL_SOURCE))
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import analyse_d3_cross_grid_temporal as analyser
import run_d3_cross_grid_temporal as runner
from airvortex.io.config_io import StrictJSONError


def _metric_flags(**overrides: bool) -> dict[str, object]:
    booleans = {
        "positive_primary": False,
        "common_reduction_primary": False,
        "increase_primary": False,
        "small_primary": False,
    }
    booleans.update(overrides)
    return {"booleans": booleans}


def _classification_payload(
    first: dict[str, object],
    second: dict[str, object],
    third: dict[str, object] | None = None,
    fourth: dict[str, object] | None = None,
) -> dict[str, dict[str, object]]:
    values = [first, second, third or _metric_flags(), fourth or _metric_flags()]
    return dict(zip(analyser.PRIMARY, values, strict=True))


def _synthetic_authorized_payload() -> dict[str, Any]:
    value = runner._json_object(runner.EXECUTION_AUTHORIZATION_PATH)
    value["status"] = runner.AUTHORIZATION_ALLOWED
    value["decision"] = {
        "real_cuda_dynamic_execution": True,
        "authorized_stages": list(runner.STAGE_ORDER),
        "automatic_follow_on": "PROHIBITED",
    }
    value["review_binding"] = {
        "reviewed_implementation_commit": "a" * 40,
        "preregistration_git_blob_sha": "b" * 40,
        "formal_source_tree_sha256": runner._contract()["formal_source_contract"][
            "required_tree_sha256"
        ],
        "implementation_files": {
            runner._relative(path): {
                "git_blob_sha": "c" * 40,
                "content_sha256": "d" * 64,
            }
            for path in runner._implementation_paths()
        },
    }
    value["ruling"] = {
        "reviewer": "ChatGPT Pro",
        "ruling_document": "90_候选工作区/current/10_D3实现静态复核与执行门裁决.md",
        "issued_at": "2026-08-31T00:00:00+08:00",
    }
    return value


class FrozenContractAndConfigurationTests(unittest.TestCase):
    def test_contract_matrix_and_current_authorization_are_exact(self) -> None:
        audit = runner.validate_contract_shape()
        self.assertTrue(audit["gate_pass"])
        self.assertTrue(audit["gates"]["case_matrix_exact"])
        self.assertTrue(audit["gates"]["current_dynamic_execution_false"])

    def test_six_configs_change_only_registered_paths(self) -> None:
        for case_id in runner.EXPECTED_CASES:
            with self.subTest(case_id=case_id):
                config, identity = runner.effective_case_config(case_id)
                self.assertTrue(identity["gate_pass"])
                self.assertLessEqual(
                    set(identity["observed_differences"]),
                    {
                        "run.duration_s",
                        "run.output_interval_s",
                        "high_fidelity.time_step_s",
                    },
                )
                self.assertEqual(config.run.duration_s, 0.010)
                self.assertEqual(config.run.output_interval_s, 0.001)
                self.assertEqual(config.drive.ramp_duration_s, 0.005)
                self.assertEqual(config.high_fidelity.pressure_solver_device, "cuda")
                self.assertEqual(config.high_fidelity.pressure_relative_tolerance, 1e-7)
                self.assertEqual(config.high_fidelity.maximum_cfl_step_retries, 4)

    def test_time_steps_are_exact_by_level_on_both_grids(self) -> None:
        expected = {
            "DT0": 5.5e-6,
            "DT1": 2.75e-6,
            "DT2": 1.375e-6,
        }
        for case_id in runner.EXPECTED_CASES:
            config, identity = runner.effective_case_config(case_id)
            self.assertEqual(
                config.high_fidelity.time_step_s,
                expected[str(identity["time_step_level"])],
            )

    def test_same_grid_physical_configuration_identity(self) -> None:
        audit = runner.configuration_matrix_audit()
        self.assertTrue(audit["gate_pass"])
        self.assertEqual(audit["same_grid_groups"], {"G6": True, "G9": True})

    def test_generated_configs_are_strict_and_exact(self) -> None:
        audit = runner.audit_generated_configs()
        self.assertTrue(audit["gate_pass"])
        self.assertTrue(audit["gates"]["exact_file_set"])

    def test_dynamic_entry_is_fail_closed_under_current_contract(self) -> None:
        with self.assertRaises(runner.D3ExecutionError):
            runner.require_dynamic_execution_authorized()
        self.assertFalse(runner.RAW_ROOT.exists())

    def test_separate_authorization_object_is_valid_and_default_denied(self) -> None:
        authorization = runner._json_object(runner.EXECUTION_AUTHORIZATION_PATH)
        audit = runner.validate_execution_authorization_shape(authorization)
        self.assertTrue(audit["gate_pass"])
        self.assertFalse(audit["authorized"])
        self.assertEqual(audit["status"], runner.AUTHORIZATION_DENIED)

    def test_missing_or_malformed_authorization_fails_before_raw_creation(self) -> None:
        with tempfile.TemporaryDirectory(dir=HERE) as temporary_directory:
            root = Path(temporary_directory)
            raw = root / "raw"
            missing = root / "missing_authorization.json"
            with (
                patch.object(runner, "EXECUTION_AUTHORIZATION_PATH", missing),
                patch.object(runner, "RAW_ROOT", raw),
            ):
                with self.assertRaises(runner.D3ExecutionError):
                    runner.require_dynamic_execution_authorized()
                self.assertFalse(raw.exists())

            malformed = root / "malformed_authorization.json"
            malformed.write_text('{"status": "NOT_AUTHORIZED",}\n', encoding="utf-8")
            with (
                patch.object(runner, "EXECUTION_AUTHORIZATION_PATH", malformed),
                patch.object(runner, "RAW_ROOT", raw),
            ):
                with self.assertRaises(StrictJSONError):
                    runner.require_dynamic_execution_authorized()
                self.assertFalse(raw.exists())

    def test_authorized_shape_requires_exact_bound_file_set_and_hashes(self) -> None:
        authorization = _synthetic_authorized_payload()
        audit = runner.validate_execution_authorization_shape(authorization)
        self.assertTrue(audit["gate_pass"])
        self.assertTrue(audit["authorized"])

        missing = deepcopy(authorization)
        missing["review_binding"]["implementation_files"].pop(
            next(iter(missing["review_binding"]["implementation_files"]))
        )
        self.assertFalse(
            runner.validate_execution_authorization_shape(missing)["gate_pass"]
        )

        drifted = deepcopy(authorization)
        first = next(iter(drifted["review_binding"]["implementation_files"].values()))
        first["git_blob_sha"] = "not-a-git-blob"
        self.assertFalse(
            runner.validate_execution_authorization_shape(drifted)["gate_pass"]
        )

    def test_authorization_audit_rejects_clean_committed_implementation_drift(
        self,
    ) -> None:
        authorization = _synthetic_authorized_payload()
        expected_binding = authorization["review_binding"]["implementation_files"]
        original_json_object = runner._json_object

        def fake_json_object(path: Path) -> dict[str, Any]:
            if path == runner.EXECUTION_AUTHORIZATION_PATH:
                return deepcopy(authorization)
            return original_json_object(path)

        def fake_blob(path: Path, revision: str = "HEAD") -> str:
            del revision
            if path == runner.PREREGISTRATION_PATH:
                return "b" * 40
            return "e" * 40

        def fake_git(*arguments: str) -> str:
            if arguments[:3] == ("rev-list", "--parents", "-n"):
                return f"{'e' * 40} {'a' * 40}"
            if arguments[:2] == ("diff", "--name-only"):
                return runner._relative(runner.EXECUTION_AUTHORIZATION_PATH)
            raise AssertionError(arguments)

        source_hash = runner._contract()["formal_source_contract"][
            "required_tree_sha256"
        ]
        with (
            patch.object(runner, "_json_object", side_effect=fake_json_object),
            patch.object(runner, "_blob_sha", side_effect=fake_blob),
            patch.object(runner, "_git", side_effect=fake_git),
            patch.object(
                runner,
                "_capture_source",
                return_value={"available": True, "value": source_hash},
            ),
            patch.object(
                runner,
                "_implementation_binding",
                return_value=deepcopy(expected_binding),
            ),
        ):
            self.assertTrue(
                runner.execution_authorization_audit(require_authorized=True)[
                    "gate_pass"
                ]
            )

        drifted_binding = deepcopy(expected_binding)
        next(iter(drifted_binding.values()))["content_sha256"] = "0" * 64
        with (
            patch.object(runner, "_json_object", side_effect=fake_json_object),
            patch.object(runner, "_blob_sha", side_effect=fake_blob),
            patch.object(runner, "_git", side_effect=fake_git),
            patch.object(
                runner,
                "_capture_source",
                return_value={"available": True, "value": source_hash},
            ),
            patch.object(
                runner,
                "_implementation_binding",
                return_value=drifted_binding,
            ),
        ):
            audit = runner.execution_authorization_audit(require_authorized=True)
            self.assertFalse(audit["gate_pass"])
            self.assertFalse(audit["gates"]["implementation_binding_matches"])

    def test_analyser_final_is_fail_closed_under_current_contract(self) -> None:
        with self.assertRaises(analyser.D3AnalysisError):
            analyser.analyse_final()
        self.assertFalse(analyser.REPORT_JSON.exists())
        self.assertFalse(analyser.REPORT_MD.exists())


class MetricArithmeticTests(unittest.TestCase):
    @staticmethod
    def _contracting_trajectories() -> dict[str, np.ndarray]:
        x = np.asarray([0.0, 1.0, 2.0])
        return {
            "G6_DT0": 0.0 * x,
            "G6_DT1": 1.0 * x,
            "G6_DT2": 1.5 * x,
            "G9_DT0": 4.0 * x,
            "G9_DT1": 3.0 * x,
            "G9_DT2": 2.5 * x,
        }

    def test_q_s_u_c_arithmetic_and_contraction(self) -> None:
        result = analyser.compute_temporal_response_metrics(
            self._contracting_trajectories(),
            epsilon=1.0e-12,
        )
        np.testing.assert_allclose(result["vectors"]["s_0"], [0.0, 4.0, 8.0])
        np.testing.assert_allclose(result["vectors"]["s_1"], [0.0, 2.0, 4.0])
        np.testing.assert_allclose(result["vectors"]["s_2"], [0.0, 1.0, 2.0])
        np.testing.assert_allclose(result["vectors"]["c_1"], [0.0, -2.0, -4.0])
        np.testing.assert_allclose(result["vectors"]["c_2"], [0.0, -1.0, -2.0])
        self.assertAlmostEqual(result["eta_delta_1"], 0.5)
        self.assertAlmostEqual(result["rho_common_1"], 0.5)
        self.assertAlmostEqual(result["rho_common_2"], 0.25)
        self.assertAlmostEqual(result["gamma_G6"], 0.5)
        self.assertAlmostEqual(result["gamma_G9"], 0.5)
        self.assertAlmostEqual(result["gamma_delta"], 0.5)
        self.assertTrue(result["booleans"]["positive_primary"])
        self.assertEqual(
            result["algebra_identity_residuals"],
            {
                "c1_minus_u9_minus_u6_max_abs": 0.0,
                "c2_minus_u9_minus_u6_max_abs": 0.0,
            },
        )

    def test_initial_removed_uses_first_value_of_supplied_trajectory(self) -> None:
        np.testing.assert_array_equal(
            analyser.initial_removed(np.asarray([7.0, 8.0, 10.0])),
            np.asarray([0.0, 1.0, 3.0]),
        )
        self.assertAlmostEqual(
            analyser.rms(np.asarray([0.0, 3.0, 4.0])),
            5.0 / math.sqrt(3.0),
        )

    def test_late_diagnostic_window_keeps_global_t0_baseline(self) -> None:
        full = np.asarray([10.0, 13.0, 18.0, 25.0])
        late = analyser.global_initial_removed_window(
            full,
            np.asarray([2, 3], dtype=np.int64),
        )
        np.testing.assert_array_equal(late, np.asarray([8.0, 15.0]))
        self.assertNotEqual(late[0], 0.0)

        trajectories = {
            case_id: late + float(index)
            for index, case_id in enumerate(analyser.EXPECTED_CASES)
        }
        result = analyser.compute_temporal_response_metrics(
            trajectories,
            epsilon=1.0e-12,
            trajectories_are_global_initial_removed=True,
        )
        self.assertEqual(
            result["initial_value_policy"],
            "GLOBAL_Q_T_MINUS_Q_0_BEFORE_WINDOW_SLICE",
        )
        self.assertEqual(result["q"]["G6_DT0"][0], 8.0)

    def test_scale_floor_controls_zero_spatial_norm(self) -> None:
        zeros = {case_id: np.zeros(4) for case_id in analyser.EXPECTED_CASES}
        result = analyser.compute_temporal_response_metrics(zeros, epsilon=1.0e-5)
        self.assertEqual(result["E_0"], 0.0)
        self.assertEqual(result["spatial_denominator"], 1.0e-5)
        self.assertEqual(result["gamma_G6"], 0.0)
        self.assertIsNone(result["cosine_G6"])
        self.assertIsNone(result["observed_order_G6"])
        self.assertTrue(result["booleans"]["increment_below_scale_G6"])

    def test_safe_cosine_is_undefined_for_either_zero_norm(self) -> None:
        self.assertIsNone(analyser.safe_cosine(np.zeros(3), np.ones(3)))
        self.assertIsNone(analyser.safe_cosine(np.ones(3), np.zeros(3)))
        cosine = analyser.safe_cosine(
            np.asarray([1.0, 0.0]),
            np.asarray([-1.0, 0.0]),
        )
        self.assertIsNotNone(cosine)
        assert cosine is not None
        self.assertAlmostEqual(cosine, -1.0)

    def test_observed_order_requires_positive_gamma_and_first_increment_scale(
        self,
    ) -> None:
        self.assertIsNone(
            analyser.observed_order(
                0.0,
                first_increment_rms=1.0,
                first_increment_scale=0.1,
            )
        )
        self.assertIsNone(
            analyser.observed_order(
                0.5,
                first_increment_rms=0.01,
                first_increment_scale=0.1,
            )
        )
        order = analyser.observed_order(
            0.5,
            first_increment_rms=1.0,
            first_increment_scale=0.1,
        )
        self.assertIsNotNone(order)
        assert order is not None
        self.assertAlmostEqual(order, 1.0)

    def test_negative_direction_cosine_blocks_non_negligible_contraction(self) -> None:
        x = np.asarray([0.0, 1.0, 2.0])
        trajectories = {
            "G6_DT0": 0.0 * x,
            "G6_DT1": 1.0 * x,
            "G6_DT2": 0.5 * x,
            "G9_DT0": 4.0 * x,
            "G9_DT1": 3.0 * x,
            "G9_DT2": 3.5 * x,
        }
        result = analyser.compute_temporal_response_metrics(
            trajectories,
            epsilon=1.0e-12,
        )
        self.assertLess(result["cosine_G6"], 0.0)
        self.assertFalse(result["booleans"]["direction_consistent_G6"])
        self.assertFalse(result["booleans"]["positive_primary"])

    def test_metric_rejects_missing_case_and_mismatched_lengths(self) -> None:
        trajectories = self._contracting_trajectories()
        missing = dict(trajectories)
        missing.pop("G9_DT2")
        with self.assertRaises(analyser.D3AnalysisError):
            analyser.compute_temporal_response_metrics(missing, epsilon=1.0e-12)
        mismatch = dict(trajectories)
        mismatch["G9_DT2"] = np.zeros(4)
        with self.assertRaises(analyser.D3AnalysisError):
            analyser.compute_temporal_response_metrics(mismatch, epsilon=1.0e-12)


class NumericalQualityHardGateTests(unittest.TestCase):
    @staticmethod
    def _passing_metrics() -> dict[str, object]:
        return {
            "complete_to_registered_end_time": True,
            "all_state_and_diagnostic_values_finite": True,
            "maximum_pressure_relative_residual": 1.0e-7,
            "maximum_projected_divergence_s_inv": 1.0e-4,
            "maximum_accepted_advective_cfl": 0.35,
            "cumulative_phase_clip_relative": 0.001,
            "cumulative_phase_repair_relative": 0.001,
            "maximum_liquid_volume_balance_error_relative": 0.001,
            "configured_time_step_active_fraction": 0.95,
            "maximum_cfl_step_retries": 4,
            "actual_cuda_without_fallback": True,
            "native_output_times_exact": True,
        }

    def test_all_registered_quality_threshold_boundaries_pass(self) -> None:
        audit = analyser.evaluate_numerical_quality_hard_gates(self._passing_metrics())
        self.assertTrue(audit["gate_pass"])
        self.assertTrue(all(audit["gates"].values()))

    def test_each_registered_quality_failure_is_individually_fail_closed(self) -> None:
        mutations = {
            "complete_to_registered_end_time": (
                False,
                "complete_to_registered_end_time",
            ),
            "all_state_and_diagnostic_values_finite": (
                False,
                "all_state_and_diagnostic_values_finite",
            ),
            "maximum_pressure_relative_residual": (
                1.1e-7,
                "pressure_relative_residual",
            ),
            "maximum_projected_divergence_s_inv": (
                1.1e-4,
                "projected_divergence",
            ),
            "maximum_accepted_advective_cfl": (0.351, "accepted_advective_cfl"),
            "cumulative_phase_clip_relative": (
                0.0011,
                "cumulative_phase_clip_relative",
            ),
            "cumulative_phase_repair_relative": (
                0.0011,
                "cumulative_phase_repair_relative",
            ),
            "maximum_liquid_volume_balance_error_relative": (
                0.0011,
                "liquid_volume_balance_error_relative",
            ),
            "configured_time_step_active_fraction": (
                0.949,
                "configured_time_step_active_fraction",
            ),
            "maximum_cfl_step_retries": (5, "maximum_cfl_step_retries"),
            "actual_cuda_without_fallback": (
                False,
                "actual_cuda_without_fallback",
            ),
            "native_output_times_exact": (False, "native_output_times_exact"),
        }
        for metric_name, (bad_value, gate_name) in mutations.items():
            with self.subTest(metric=metric_name):
                metrics = self._passing_metrics()
                metrics[metric_name] = bad_value
                audit = analyser.evaluate_numerical_quality_hard_gates(metrics)
                self.assertFalse(audit["gate_pass"])
                self.assertFalse(audit["gates"][gate_name])

    def test_nonfinite_quality_value_fails_closed(self) -> None:
        metrics = self._passing_metrics()
        metrics["maximum_pressure_relative_residual"] = math.nan
        audit = analyser.evaluate_numerical_quality_hard_gates(metrics)
        self.assertFalse(audit["gate_pass"])
        self.assertFalse(audit["gates"]["pressure_relative_residual"])

    def test_quality_metric_set_cannot_be_silently_incomplete(self) -> None:
        metrics = self._passing_metrics()
        metrics.pop("native_output_times_exact")
        with self.assertRaises(analyser.D3AnalysisError):
            analyser.evaluate_numerical_quality_hard_gates(metrics)

    def test_runner_per_step_gate_rejects_each_dense_step_violation(self) -> None:
        runtime: Any = SimpleNamespace(
            state=np.zeros(1),
            initial_volume_m3=1.0,
            pressure_accelerator={"selected": "cuda", "fallback": False},
        )
        passing = {
            "pressure_relative_residual": 1.0e-7,
            "divergence_after_s_inv": 1.0e-4,
            "advective_cfl": 0.35,
            "liquid_volume_balance_error_m3": 0.001,
            "cfl_retry_count": 4,
        }
        diagnostics: Any = SimpleNamespace(**passing)
        passed, failed = runner._step_hard_gate(runtime, diagnostics)
        self.assertTrue(passed)
        self.assertEqual(failed, [])
        mutations = {
            "pressure_relative_residual": 1.1e-7,
            "divergence_after_s_inv": 1.1e-4,
            "advective_cfl": 0.351,
            "liquid_volume_balance_error_m3": 0.0011,
            "cfl_retry_count": 5,
        }
        for name, bad_value in mutations.items():
            with self.subTest(name=name):
                values = dict(passing)
                values[name] = bad_value
                diagnostics = SimpleNamespace(**values)
                passed, failed = runner._step_hard_gate(runtime, diagnostics)
                self.assertFalse(passed)
                self.assertTrue(failed)


class ClassificationPrecedenceTests(unittest.TestCase):
    def test_hard_gate_failure_has_absolute_precedence(self) -> None:
        metrics = _classification_payload(
            _metric_flags(positive_primary=True, common_reduction_primary=True),
            _metric_flags(positive_primary=True, common_reduction_primary=True),
        )
        label, detail = analyser.classify_top_level(metrics, hard_gate_pass=False)
        self.assertEqual(label, analyser.FAIL_CLOSED)
        self.assertEqual(detail["precedence_winner"], analyser.FAIL_CLOSED)

    def test_contracting_reduction_precedes_plain_reduction(self) -> None:
        metrics = _classification_payload(
            _metric_flags(positive_primary=True, common_reduction_primary=True),
            _metric_flags(positive_primary=True, common_reduction_primary=True),
            _metric_flags(common_reduction_primary=True),
        )
        label, _ = analyser.classify_top_level(metrics, hard_gate_pass=True)
        self.assertEqual(
            label,
            "COMMON_DT_REDUCTION_WITH_CONTRACTING_TEMPORAL_RESPONSE",
        )

    def test_plain_reduction_precedes_increase(self) -> None:
        metrics = _classification_payload(
            _metric_flags(common_reduction_primary=True),
            _metric_flags(common_reduction_primary=True),
            _metric_flags(increase_primary=True),
            _metric_flags(increase_primary=True),
        )
        label, _ = analyser.classify_top_level(metrics, hard_gate_pass=True)
        self.assertEqual(label, "COMMON_DT_REDUCTION_WITHOUT_REGISTERED_CONTRACTION")

    def test_increase_label_requires_two_primary_observables(self) -> None:
        metrics = _classification_payload(
            _metric_flags(increase_primary=True),
            _metric_flags(increase_primary=True),
        )
        label, _ = analyser.classify_top_level(metrics, hard_gate_pass=True)
        self.assertEqual(label, "COMMON_DT_INCREASE_SIGNAL")

    def test_small_label_requires_all_four_primary_observables(self) -> None:
        all_small = _classification_payload(
            _metric_flags(small_primary=True),
            _metric_flags(small_primary=True),
            _metric_flags(small_primary=True),
            _metric_flags(small_primary=True),
        )
        label, _ = analyser.classify_top_level(all_small, hard_gate_pass=True)
        self.assertEqual(
            label,
            "DIFFERENTIAL_TEMPORAL_EFFECT_SMALL_RELATIVE_TO_SPATIAL_GAP",
        )
        only_three = deepcopy(all_small)
        only_three[analyser.PRIMARY[-1]] = _metric_flags()
        label, _ = analyser.classify_top_level(only_three, hard_gate_pass=True)
        self.assertEqual(label, "MIXED_OR_INDETERMINATE_CROSS_GRID_TEMPORAL_RESPONSE")

    def test_exact_primary_set_is_mandatory(self) -> None:
        with self.assertRaises(analyser.D3AnalysisError):
            analyser.classify_top_level(
                {"only_one": _metric_flags()},
                hard_gate_pass=True,
            )


class IdentityFreshnessAndLedgerTests(unittest.TestCase):
    def test_formal_g0_mask_rejects_one_flipped_cell(self) -> None:
        rebuilt = np.asarray([[0, 1], [1, 0]], dtype=np.uint8)
        archived = rebuilt.copy()
        archived[0, 0] = 1
        audit = analyser.audit_formal_g0_mask(rebuilt, archived)
        self.assertFalse(audit["gate_pass"])
        self.assertFalse(audit["gates"]["elementwise_equal"])

    def test_runner_input_path_rejects_d2_and_checkpoint_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / "d3_raw"
            root.mkdir()
            good = root / "cases" / "G6_DT0" / "archive"
            d2 = root / "d2_source_attribution" / "archive"
            checkpoint = root / "cases" / "checkpoint.npz"
            good.mkdir(parents=True)
            d2.mkdir(parents=True)
            checkpoint.parent.mkdir(parents=True, exist_ok=True)
            checkpoint.write_bytes(b"x")
            self.assertTrue(
                runner.audit_classification_input_paths([good], allowed_root=root)[
                    "gate_pass"
                ]
            )
            self.assertFalse(
                runner.audit_classification_input_paths([d2], allowed_root=root)[
                    "gate_pass"
                ]
            )
            self.assertFalse(
                runner.audit_classification_input_paths(
                    [checkpoint], allowed_root=root
                )["gate_pass"]
            )

    def test_analyser_input_path_rejects_outside_root_and_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            base = Path(temporary_directory)
            root = base / "raw"
            root.mkdir()
            outside = base / "outside"
            outside.mkdir()
            link = root / "linked"
            link.symlink_to(outside, target_is_directory=True)
            self.assertFalse(
                analyser.audit_archive_input_path(outside, allowed_root=root)[
                    "gate_pass"
                ]
            )
            self.assertFalse(
                analyser.audit_archive_input_path(link, allowed_root=root)["gate_pass"]
            )

    def test_case_summary_payload_binding_rejects_extra_or_changed_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            archive = Path(temporary_directory)
            first = archive / "result.json"
            first.write_text("{}\n", encoding="utf-8")
            record = {"payloads": analyser._archive_payloads(archive)}
            self.assertTrue(
                analyser.audit_summary_archive_payloads(archive, record)["gate_pass"]
            )
            (archive / "extra.json").write_text("{}\n", encoding="utf-8")
            audit = analyser.audit_summary_archive_payloads(archive, record)
            self.assertFalse(audit["gate_pass"])
            self.assertFalse(audit["gates"]["exact_file_set_hash_and_size"])

    def test_analyser_independently_rebuilds_initial_identity_not_runner_boolean(
        self,
    ) -> None:
        def fake_config(case_id: str) -> dict[str, object]:
            return {"case_id": case_id}

        def fake_grid(case_id: str) -> SimpleNamespace:
            size = 2 if case_id.startswith("G6_") else 3
            mask = np.zeros((size, size), dtype=bool)
            mask[0, 0] = True
            return SimpleNamespace(bar_mask=mask, active_mask=np.ones_like(mask))

        def fake_state() -> runner.HF3DState:
            array = np.zeros((2, 2), dtype=np.float64)
            return runner.HF3DState(
                time_s=0.0,
                step=0,
                bar_angle_rad=0.0,
                u_x_face=array.copy(),
                u_y_face=array.copy(),
                u_z_face=array.copy(),
                phase=array.copy(),
                pressure_pa=array.copy(),
            )

        class FakeBackend:
            def make_grid(self, config: SimpleNamespace) -> SimpleNamespace:
                return fake_grid(str(config.case_id))

            def initialise_state(
                self,
                config: SimpleNamespace,
                grid: SimpleNamespace,
            ) -> runner.HF3DState:
                del config, grid
                return fake_state()

        s0_cases: dict[str, Any] = {}
        runs: dict[str, Any] = {}
        for case_id in analyser.EXPECTED_CASES:
            grid = fake_grid(case_id)
            state = fake_state()
            config = fake_config(case_id)
            record = analyser._initial_identity_record(
                grid,
                state,
                config_canonical_sha256=analyser._canonical_sha256(config),
            )
            s0_cases[case_id] = record
            runs[case_id] = SimpleNamespace(
                summary={
                    "initial_identity": deepcopy(record),
                    "gates": {"initial_grid_mask_state_matches_s0": False},
                },
                fields={"stir_bar_mask_3d": grid.bar_mask.astype(np.uint8)},
            )
        s0 = {"initial_grid_mask_state_identity": {"cases": s0_cases}}
        with (
            patch.object(analyser, "HighFidelityBackend", FakeBackend),
            patch.object(analyser, "_expected_config", side_effect=fake_config),
            patch.object(
                analyser.SimulationConfig,
                "from_dict",
                side_effect=lambda value: SimpleNamespace(case_id=value["case_id"]),
            ),
        ):
            audit = analyser.independent_initial_identity_audit(runs, s0)
            self.assertTrue(audit["gate_pass"])
            self.assertEqual(audit["time_steps_advanced"], 0)

            drifted_runs = deepcopy(runs)
            drifted_runs["G6_DT1"].summary["initial_identity"]["state"]["phase"][
                "sha256"
            ] = "0" * 64
            drifted_runs["G6_DT1"].summary["gates"][
                "initial_grid_mask_state_matches_s0"
            ] = True
            drifted = analyser.independent_initial_identity_audit(drifted_runs, s0)
            self.assertFalse(drifted["gate_pass"])
            self.assertFalse(
                drifted["cases"]["G6_DT1"]["gates"]["case_summary_matches_s0_exactly"]
            )

    @staticmethod
    def _valid_provenance(commit: str, source: str) -> dict[str, Any]:
        return {
            "source_identity": {
                "disk_content_tree": {
                    "boundary_match": True,
                    "execution_start": {"value": source},
                    "execution_end": {"value": source},
                },
                "git_revision": {
                    "boundary_match": True,
                    "execution_start": {"value": commit},
                    "execution_end": {"value": commit},
                },
            },
            "extra": {
                "candidate_protocol_id": analyser.PROTOCOL_ID,
                "fresh_from_zero": True,
                "checkpoint_used": False,
                "d2_dynamic_input_used": False,
            },
            "invocation": {"entry_point": "run_d3_cross_grid_temporal.py"},
        }

    def test_cross_commit_provenance_is_rejected(self) -> None:
        commit = "a" * 40
        source = "b" * 64
        valid = self._valid_provenance(commit, source)
        audit = analyser._provenance_revision_audit(
            valid,
            execution_commit=commit,
            source_sha256=source,
        )
        self.assertTrue(audit["gate_pass"])
        wrong = deepcopy(valid)
        wrong["source_identity"]["git_revision"]["execution_end"]["value"] = "c" * 40
        audit = analyser._provenance_revision_audit(
            wrong,
            execution_commit=commit,
            source_sha256=source,
        )
        self.assertFalse(audit["gate_pass"])
        self.assertFalse(audit["gates"]["revision_end_matches"])

    def test_provenance_with_d2_reference_is_rejected(self) -> None:
        commit = "a" * 40
        source = "b" * 64
        provenance = self._valid_provenance(commit, source)
        provenance["extra"]["source_path"] = "06_D2/source/archive"
        audit = analyser._provenance_revision_audit(
            provenance,
            execution_commit=commit,
            source_sha256=source,
        )
        self.assertFalse(audit["gate_pass"])
        self.assertFalse(audit["gates"]["no_forbidden_reference"])

    def test_stage_ledger_actions_and_descriptions_are_exact(self) -> None:
        ledger = runner.stage_ledger_template(execution_commit=None)
        audit = runner.validate_stage_ledger_semantics(ledger)
        self.assertTrue(audit["gate_pass"])
        swapped = deepcopy(ledger)
        swapped["stages"][1]["action"] = runner.STAGE_ACTIONS["S2"]
        audit = runner.validate_stage_ledger_semantics(swapped)
        self.assertFalse(audit["gate_pass"])
        self.assertFalse(audit["stages"]["S1"]["gates"]["action_exact"])

    def test_not_executed_stage_cannot_claim_evidence(self) -> None:
        ledger = runner.stage_ledger_template(execution_commit=None)
        ledger["stages"][3]["evidence"] = ["reports/future.json"]
        audit = runner.validate_stage_ledger_semantics(ledger)
        self.assertFalse(audit["gate_pass"])
        self.assertFalse(audit["stages"]["S3"]["gates"]["not_executed_has_no_evidence"])

    def test_s1_s2_descriptions_cannot_be_semantically_swapped(self) -> None:
        ledger = runner.stage_ledger_template(execution_commit=None)
        ledger["stages"][1]["human_description"] = runner.STAGE_DESCRIPTIONS["S2"]
        ledger["stages"][2]["human_description"] = runner.STAGE_DESCRIPTIONS["S1"]
        audit = runner.validate_stage_ledger_semantics(ledger)
        self.assertFalse(audit["gate_pass"])
        self.assertFalse(audit["stages"]["S1"]["gates"]["human_description_exact"])
        self.assertFalse(audit["stages"]["S2"]["gates"]["human_description_exact"])

    def test_stage_ledger_rejects_duplicate_reorder_and_illegal_state_pairs(
        self,
    ) -> None:
        mutations: list[tuple[str, Any]] = []
        duplicate = runner.stage_ledger_template(execution_commit="a" * 40)
        duplicate["stages"][2]["stage"] = "S1"
        mutations.append(("duplicate", duplicate))
        reordered = runner.stage_ledger_template(execution_commit="a" * 40)
        reordered["stages"][1], reordered["stages"][2] = (
            reordered["stages"][2],
            reordered["stages"][1],
        )
        mutations.append(("reordered", reordered))
        illegal = runner.stage_ledger_template(execution_commit="a" * 40)
        illegal["stages"][0]["disposition"] = "EXECUTED"
        illegal["stages"][0]["status"] = "NOT_EXECUTED"
        mutations.append(("illegal_state_pair", illegal))
        for name, ledger in mutations:
            with self.subTest(name=name):
                self.assertFalse(
                    runner.validate_stage_ledger_semantics(ledger)["gate_pass"]
                )

    def test_stage_ledger_requires_exact_ordered_evidence_paths(self) -> None:
        ledger = runner.stage_ledger_template(execution_commit="a" * 40)
        ledger["stages"][0] = runner._execution_stage_entry(
            "S0",
            disposition="EXECUTED",
            status="PASS",
            evidence=runner._stage_pass_evidence("S0"),
        )
        runner._sync_stage_ledger_summary(ledger)
        self.assertTrue(runner.validate_stage_ledger_semantics(ledger)["gate_pass"])

        wrong = deepcopy(ledger)
        wrong["stages"][0]["evidence"] = ["wrong/s0.json"]
        audit = runner.validate_stage_ledger_semantics(wrong)
        self.assertFalse(audit["gate_pass"])
        self.assertFalse(audit["stages"]["S0"]["gates"]["evidence_paths_exact"])

    def test_stage_ledger_allows_one_failed_stage_then_only_unexecuted(self) -> None:
        ledger = runner.stage_ledger_template(execution_commit="a" * 40)
        ledger["stages"][0] = runner._execution_stage_entry(
            "S0",
            disposition="EXECUTED",
            status="PASS",
            evidence=runner._stage_pass_evidence("S0"),
        )
        ledger["stages"][1] = runner._execution_stage_entry(
            "S1",
            disposition="FAILED_STOPPED",
            status=runner.FAIL_CLOSED,
            evidence=[runner._relative(runner.FAILURE_PATH)],
        )
        runner._sync_stage_ledger_summary(ledger)
        audit = runner.validate_stage_ledger_semantics(ledger)
        self.assertTrue(audit["gate_pass"])
        self.assertEqual(ledger["failed_stages"], ["S1"])
        self.assertEqual(ledger["unexecuted_stages"], ["S2", "S3"])

        invalid = deepcopy(ledger)
        invalid["stages"][2] = runner._execution_stage_entry(
            "S2",
            disposition="EXECUTED",
            status="PASS",
            evidence=runner._stage_pass_evidence("S2"),
        )
        runner._sync_stage_ledger_summary(invalid)
        self.assertFalse(runner.validate_stage_ledger_semantics(invalid)["gate_pass"])

    def test_unique_failure_transaction_keeps_marker_and_ledger_consistent(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(dir=HERE) as temporary_directory:
            root = Path(temporary_directory)
            ledger_path = root / "stage_ledger.json"
            failure_path = root / "execution_failure.json"
            with (
                patch.object(runner, "RAW_ROOT", root),
                patch.object(runner, "STAGE_LEDGER_PATH", ledger_path),
                patch.object(runner, "FAILURE_PATH", failure_path),
            ):
                runner._write_json(
                    ledger_path,
                    runner.stage_ledger_template(execution_commit="a" * 40),
                )
                recorded = runner._record_dynamic_failure(
                    "S0",
                    ["prepare-execution"],
                    RuntimeError("injected preparation failure"),
                )
                self.assertTrue(recorded)
                ledger = runner._json_object(ledger_path)
                failure = runner._json_object(failure_path)
                self.assertTrue(
                    runner.validate_stage_ledger_semantics(ledger)["gate_pass"]
                )
                self.assertTrue(
                    runner.validate_stage_ledger_evidence_files(ledger)["gate_pass"]
                )
                self.assertEqual(ledger["failed_stages"], ["S0"])
                self.assertEqual(failure["stage"], "S0")
                self.assertEqual(failure["execution_commit"], "a" * 40)

    def test_prepare_exception_is_routed_to_unique_failure_transaction(self) -> None:
        with tempfile.TemporaryDirectory(dir=HERE) as temporary_directory:
            root = Path(temporary_directory)
            ledger_path = root / "stage_ledger.json"
            failure_path = root / "execution_failure.json"
            s0_path = root / "s0.json"
            with (
                patch.object(runner, "RAW_ROOT", root),
                patch.object(runner, "STAGE_LEDGER_PATH", ledger_path),
                patch.object(runner, "FAILURE_PATH", failure_path),
                patch.object(runner, "S0_PATH", s0_path),
                patch.object(
                    runner,
                    "execution_identity_audit",
                    return_value={"execution_commit": "a" * 40},
                ),
                patch.object(
                    runner,
                    "initial_grid_state_identity_audit",
                    side_effect=RuntimeError("injected initial rebuild failure"),
                ),
            ):
                with self.assertRaises(RuntimeError):
                    runner.main(["prepare-execution"])
                ledger = runner._json_object(ledger_path)
                self.assertEqual(ledger["failed_stages"], ["S0"])
                self.assertEqual(
                    runner._json_object(failure_path)["stage"],
                    "S0",
                )

    def test_archive_and_summary_exceptions_are_wrapped_as_case_transactions(
        self,
    ) -> None:
        temporary_directory = tempfile.TemporaryDirectory(dir=HERE)
        self.addCleanup(temporary_directory.cleanup)
        summary_path = Path(temporary_directory.name) / "case_summary.json"
        runtime = SimpleNamespace(
            grid=SimpleNamespace(),
            state=SimpleNamespace(),
            config_identity={"effective_canonical_sha256": "f" * 64},
            pressure_accelerator={"selected": "cuda", "fallback": False},
        )
        binding = {
            "execution_commit": "a" * 40,
            "implementation": {},
            "authorization": {"observed": {}},
        }
        source_hash = runner._contract()["formal_source_contract"][
            "required_tree_sha256"
        ]
        outcome = {
            "completed": True,
            "failure": None,
            "retry_attempts": 0,
            "wall_time_s": 1.0,
            "process_cpu_time_s": 1.0,
        }
        original_json_object = runner._json_object

        def fake_case_json(path: Path) -> dict[str, Any]:
            if path == runner.S0_PATH:
                return {
                    "initial_grid_mask_state_identity": {
                        "cases": {"G6_DT0": {"identity": "exact"}}
                    }
                }
            return original_json_object(path)

        common_patches: tuple[Any, ...] = (
            patch.object(
                runner,
                "_case_summary_path",
                return_value=summary_path,
            ),
            patch.object(runner, "execution_binding_audit", return_value=binding),
            patch.object(runner, "_prepare_runtime", return_value=runtime),
            patch.object(
                runner,
                "_initial_identity_record",
                return_value={"identity": "exact"},
            ),
            patch.object(
                runner,
                "_json_object",
                side_effect=fake_case_json,
            ),
            patch.object(
                runner,
                "_capture_source",
                return_value={"value": source_hash},
            ),
            patch.object(
                runner,
                "_capture_revision",
                return_value={"value": "a" * 40},
            ),
            patch.object(
                runner,
                "advance_runtime_to_registered_end",
                return_value=outcome,
            ),
            patch.object(
                runner,
                "_runtime_quality",
                return_value={"gate_pass": True},
            ),
            patch.object(
                runner,
                "_save_partial",
                return_value={"path": "recoverable/partial.npz"},
            ),
        )
        with ExitStack() as stack:
            for manager in common_patches:
                stack.enter_context(manager)
            stack.enter_context(
                patch.object(
                    runner,
                    "_assemble_archive",
                    side_effect=RuntimeError("injected archive failure"),
                )
            )
            with self.assertRaises(runner.D3CaseTransactionError) as archive_error:
                runner._run_one_case(
                    "G6_DT0",
                    {"execution_commit": "a" * 40},
                )
            self.assertIn("archive failure", str(archive_error.exception))

        common_patches = (
            patch.object(
                runner,
                "_case_summary_path",
                return_value=summary_path,
            ),
            patch.object(runner, "execution_binding_audit", return_value=binding),
            patch.object(runner, "_prepare_runtime", return_value=runtime),
            patch.object(
                runner,
                "_initial_identity_record",
                return_value={"identity": "exact"},
            ),
            patch.object(
                runner,
                "_json_object",
                side_effect=fake_case_json,
            ),
            patch.object(
                runner,
                "_capture_source",
                return_value={"value": source_hash},
            ),
            patch.object(
                runner,
                "_capture_revision",
                return_value={"value": "a" * 40},
            ),
            patch.object(
                runner,
                "advance_runtime_to_registered_end",
                return_value=outcome,
            ),
            patch.object(
                runner,
                "_runtime_quality",
                return_value={"gate_pass": True},
            ),
            patch.object(
                runner,
                "_assemble_archive",
                return_value={"path": "archive"},
            ),
            patch.object(
                runner,
                "_save_partial",
                return_value={"path": "recoverable/partial.npz"},
            ),
            patch.object(
                runner,
                "_write_json",
                side_effect=RuntimeError("injected summary failure"),
            ),
        )
        with ExitStack() as stack:
            for manager in common_patches:
                stack.enter_context(manager)
            with self.assertRaises(runner.D3CaseTransactionError) as summary_error:
                runner._run_one_case(
                    "G6_DT0",
                    {"execution_commit": "a" * 40},
                )
            self.assertIn("summary failure", str(summary_error.exception))

    def test_stage_commit_exception_is_routed_to_failure_transaction(self) -> None:
        with tempfile.TemporaryDirectory(dir=HERE) as temporary_directory:
            root = Path(temporary_directory)
            ledger_path = root / "stage_ledger.json"
            failure_path = root / "execution_failure.json"
            s0_path = root / "s0.json"
            s0_path.write_text("{}\n", encoding="utf-8")
            original_replace = runner._replace_stage_entry
            calls = 0

            def fail_first_stage_commit(
                stage: str,
                *,
                disposition: str,
                status: str,
                evidence: Any,
            ) -> None:
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise RuntimeError("injected stage commit failure")
                original_replace(
                    stage,
                    disposition=disposition,
                    status=status,
                    evidence=evidence,
                )

            with (
                patch.object(runner, "RAW_ROOT", root),
                patch.object(runner, "STAGE_LEDGER_PATH", ledger_path),
                patch.object(runner, "FAILURE_PATH", failure_path),
                patch.object(runner, "S0_PATH", s0_path),
                patch.object(
                    runner,
                    "execution_identity_audit",
                    return_value={"execution_commit": "a" * 40},
                ),
                patch.object(
                    runner,
                    "_run_one_case",
                    return_value={"gate_pass": True},
                ),
                patch.object(
                    runner,
                    "_replace_stage_entry",
                    side_effect=fail_first_stage_commit,
                ),
            ):
                ledger = runner.stage_ledger_template(execution_commit="a" * 40)
                ledger["stages"][0] = runner._execution_stage_entry(
                    "S0",
                    disposition="EXECUTED",
                    status="PASS",
                    evidence=[runner._relative(s0_path)],
                )
                runner._sync_stage_ledger_summary(ledger)
                runner._write_json(ledger_path, ledger)
                with self.assertRaises(RuntimeError):
                    runner.main(["run-stage", "--stage", "S1"])
                failed_ledger = runner._json_object(ledger_path)
                self.assertEqual(failed_ledger["failed_stages"], ["S1"])
                self.assertEqual(
                    runner._json_object(failure_path)["stage"],
                    "S1",
                )


if __name__ == "__main__":
    unittest.main()
