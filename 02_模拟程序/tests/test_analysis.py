from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from airvortex.analysis import (
    CALIBRATION,
    SEALED_VALIDATION,
    CalibrationGuard,
    ColumnSpec,
    ConnectivityState,
    DataValidationError,
    ExperimentDataset,
    NoCommonIntervalError,
    ObservableSeries,
    RoleViolationError,
    SweepAxis,
    aggregate_phase_data,
    burgers_finite_container_factors,
    classify_connectivity,
    classify_experiment_window,
    compare_results,
    compare_series,
    compare_thresholds,
    compute_dimensionless_numbers,
    dimensionless_from_config,
    generate_sweep_plan,
    load_experiment,
    save_experiment,
    series_from_result,
)
from airvortex.core import (
    BackendMode,
    RunStatus,
    SimulationConfig,
    SimulationResult,
)


def make_dataset(role: str) -> ExperimentDataset:
    return ExperimentDataset(
        time_s=np.array([0.0, 0.5, 1.0]),
        observables={"surface_depth": np.array([0.0, 0.001, 0.002])},
        role=role,
        columns=(
            ColumnSpec("time_ms", "time", "ms", "time"),
            ColumnSpec("depth_mm", "surface_depth", "mm", "observable"),
        ),
        metadata={"run_id": "test-run"},
    )


class DimensionlessTests(unittest.TestCase):
    def test_bond_identity_and_ohnesorge(self) -> None:
        groups = compute_dimensionless_numbers(
            density=998.2,
            dynamic_viscosity=1.002e-3,
            surface_tension=0.072,
            angular_speed=90.0,
            bar_half_length=0.015,
            gravity=9.80665,
            container_radius=0.045,
            liquid_depth=0.060,
            bar_diameter=0.006,
            bar_submergence=0.0525,
        )
        self.assertAlmostEqual(groups.Bo, groups.We / groups.Fr, places=13)
        self.assertAlmostEqual(groups.Oh, np.sqrt(groups.We) / groups.Re, places=13)
        self.assertEqual(groups.geometry.as_dict()["H/R"], 0.060 / 0.045)
        self.assertIsNone(groups.We_l)
        self.assertTrue(any("We_l unavailable" in item for item in groups.unavailable))

    def test_core_config_adapter_and_structural_groups(self) -> None:
        config = SimulationConfig()
        groups = dimensionless_from_config(config)
        omega = config.drive.target_rpm * 2.0 * np.pi / 60.0
        burgers_coefficient = config.forward.circulation_coupling_m2 * omega
        submergence = (
            config.container.liquid_depth_m
            - config.stir_bar.bottom_clearance_m
            - 2.0 * config.stir_bar.radius_m
        )
        expected = (
            np.log(2.0)
            * burgers_coefficient**2
            / (
                config.forward.effective_core_radius_m**2
                * config.fluid.gravity_m_s2
                * submergence
            )
        )
        self.assertAlmostEqual(groups.Pi_touch, expected)
        self.assertAlmostEqual(groups.X, 20.25, places=14)
        self.assertAlmostEqual(groups.F_X, 0.964377900230123094, places=14)
        self.assertAlmostEqual(groups.K_X, 0.103027066182219748, places=14)
        self.assertAlmostEqual(groups.G_X, 0.8613508340479033, places=14)
        self.assertAlmostEqual(groups.F_X, groups.G_X + groups.K_X, places=15)
        self.assertAlmostEqual(
            groups.Pi_touch_finite,
            expected * 0.8613508340479033,
            places=14,
        )
        self.assertIn(
            "not experimentally validated", groups.metadata["evidence_status"]
        )
        self.assertTrue(any("Capillarity" in note for note in groups.notes))
        self.assertIsNone(groups.We_l)
        local = dimensionless_from_config(
            config,
            local_relative_speed=0.2,
            local_air_core_radius=0.001,
        )
        self.assertAlmostEqual(
            local.We_l,
            config.fluid.liquid_density_kg_m3 * 0.2**2 * 0.001
            / config.fluid.surface_tension_n_m,
        )

    def test_finite_container_small_x_limits_are_stable(self) -> None:
        x = 1.0e-10
        factors = burgers_finite_container_factors(x)
        log_two = np.log(2.0)
        self.assertAlmostEqual(factors.X, x)
        self.assertAlmostEqual(
            factors.F / (x / (2.0 * log_two)), 1.0, places=9
        )
        self.assertAlmostEqual(
            factors.K / (x / (4.0 * log_two)), 1.0, places=9
        )
        self.assertAlmostEqual(
            factors.G / (x / (4.0 * log_two)), 1.0, places=9
        )
        self.assertAlmostEqual(factors.F, factors.G + factors.K, places=24)

    def test_finite_container_large_x_limits(self) -> None:
        x = 1.0e6
        factors = burgers_finite_container_factors(x)
        denominator = 2.0 * np.log(2.0) * x
        expected_f = 1.0 - 1.0 / denominator
        expected_k = (np.euler_gamma + np.log(x / 2.0)) / denominator
        expected_g = expected_f - expected_k
        self.assertAlmostEqual(factors.F, expected_f, places=15)
        self.assertAlmostEqual(factors.K, expected_k, places=15)
        self.assertAlmostEqual(factors.G, expected_g, places=15)

    def test_full_circulation_is_exactly_two_pi_times_C(self) -> None:
        common = dict(
            density=998.2,
            dynamic_viscosity=1.002e-3,
            surface_tension=0.072,
            angular_speed=90.0,
            bar_half_length=0.015,
            gravity=9.80665,
            container_radius=0.045,
            liquid_depth=0.060,
            bar_diameter=0.006,
            bar_submergence=0.0525,
            vortex_core_radius=0.010,
        )
        coefficient = 8.25e-3
        from_c = compute_dimensionless_numbers(
            **common, burgers_coefficient=coefficient
        )
        from_gamma = compute_dimensionless_numbers(
            **common, full_circulation=2.0 * np.pi * coefficient
        )
        self.assertAlmostEqual(from_c.Pi_touch, from_gamma.Pi_touch, places=15)
        self.assertAlmostEqual(
            from_c.Pi_touch_finite, from_gamma.Pi_touch_finite, places=15
        )

    def test_finite_factor_and_local_speed_validation(self) -> None:
        with self.assertRaises(DataValidationError):
            burgers_finite_container_factors(0.0)
        with self.assertRaises(DataValidationError):
            compute_dimensionless_numbers(
                density=998.2,
                dynamic_viscosity=1.002e-3,
                surface_tension=0.072,
                angular_speed=90.0,
                bar_half_length=0.015,
                gravity=9.80665,
                container_radius=0.045,
                liquid_depth=0.060,
                bar_diameter=0.006,
                bar_submergence=0.0525,
                local_relative_speed=-0.2,
                local_air_core_radius=0.001,
            )


from validity_fixtures import synthetic_signal_result


class ComparisonTests(unittest.TestCase):
    def test_different_time_grids_are_explicitly_aligned(self) -> None:
        left = synthetic_signal_result(
            backend=BackendMode.FORWARD,
            time_s=np.array([0.0, 0.7, 2.0]),
            observables={"surface_depth": np.array([0.0, 1.4, 4.0])},
            metadata={"observable_units": {"surface_depth": "m"}},
        )
        right = synthetic_signal_result(
            backend=BackendMode.HIGH_FIDELITY,
            time_s=np.array([0.25, 1.0, 1.5, 2.5]),
            observables={"surface_depth": np.array([0.5, 2.0, 3.0, 5.0])},
            metadata={"observable_units": {"surface_depth": "m"}},
        )
        comparison = compare_results(
            left, right, "surface_depth", threshold=2.0
        )
        self.assertEqual(comparison.common_interval, (0.25, 2.0))
        np.testing.assert_array_equal(left.time_s, [0.0, 0.7, 2.0])
        np.testing.assert_allclose(comparison.residual, 0.0, atol=1e-14)
        self.assertAlmostEqual(comparison.metrics.rmse, 0.0)
        self.assertAlmostEqual(comparison.metrics.mae, 0.0)
        self.assertAlmostEqual(comparison.metrics.nrmse, 0.0)
        self.assertAlmostEqual(comparison.metrics.max_error, 0.0)
        self.assertAlmostEqual(comparison.metrics.threshold_error, 0.0)

    def test_no_positive_duration_common_interval_is_rejected(self) -> None:
        left = ObservableSeries(np.array([0.0, 1.0]), np.array([0.0, 1.0]), "m", "a")
        right = ObservableSeries(np.array([1.0, 2.0]), np.array([1.0, 2.0]), "m", "b")
        with self.assertRaises(NoCommonIntervalError):
            compare_series(left, right)

    def test_unit_mismatch_is_rejected(self) -> None:
        left = ObservableSeries(np.array([0.0, 1.0]), np.array([0.0, 1.0]), "m", "a")
        right = ObservableSeries(np.array([0.0, 1.0]), np.array([0.0, 1.0]), "mm", "b")
        with self.assertRaises(DataValidationError):
            compare_series(left, right)
        for placeholder in ("SI", "unknown", "unitless", "?"):
            with self.subTest(placeholder=placeholder):
                with self.assertRaises(DataValidationError):
                    ObservableSeries(
                        np.array([0.0, 1.0]),
                        np.array([0.0, 1.0]),
                        placeholder,
                        "direct",
                    )

    def test_zero_normalization_scale_is_explicitly_undefined_not_infinite(self) -> None:
        time = np.array([0.0, 1.0, 2.0])
        reference = ObservableSeries(time, np.ones(3), "m", "reference")
        biased = ObservableSeries(time, np.full(3, 1.5), "m", "biased")
        comparison = compare_series(biased, reference)
        self.assertIsNone(comparison.metrics.nrmse)
        self.assertEqual(comparison.metrics.normalization_scale, 0.0)
        self.assertFalse(comparison.metrics.normalization_defined)
        self.assertAlmostEqual(comparison.metrics.rmse, 0.5)
        self.assertIsNone(comparison.metrics.as_dict()["nrmse"])

        identical = compare_series(reference, reference)
        self.assertIsNone(identical.metrics.nrmse)
        self.assertFalse(identical.metrics.normalization_defined)
        self.assertEqual(identical.metrics.rmse, 0.0)

    def test_missing_result_units_are_rejected_even_on_both_sides(self) -> None:
        left = synthetic_signal_result(
            backend=BackendMode.FORWARD,
            time_s=np.array([0.0, 1.0]),
            observables={"surface_depth": np.array([0.0, 0.001])},
            metadata={},
        )
        right = synthetic_signal_result(
            backend=BackendMode.HIGH_FIDELITY,
            time_s=np.array([0.0, 1.0]),
            observables={"surface_depth": np.array([0.0, 0.001])},
            metadata={},
        )
        with self.assertRaisesRegex(
            DataValidationError, "no explicit unit"
        ):
            compare_results(left, right, "surface_depth")

        right_with_unit = synthetic_signal_result(
            backend=BackendMode.HIGH_FIDELITY,
            time_s=np.array([0.0, 1.0]),
            observables={"surface_depth": np.array([0.0, 0.001])},
            metadata={"observable_units": {"surface_depth": "m"}},
        )
        with self.assertRaisesRegex(
            DataValidationError, "no explicit unit"
        ):
            compare_results(left, right_with_unit, "surface_depth")

    def test_invalid_explicit_result_units_are_never_stringified(self) -> None:
        for invalid_unit in (
            None,
            1,
            {},
            "",
            "   ",
            "SI",
            "si",
            "unknown",
            "unitless",
            "dimensionless",
            "unspecified",
            "n/a",
            "?",
            "-",
        ):
            with self.subTest(unit=invalid_unit):
                result = synthetic_signal_result(
                    backend=BackendMode.FORWARD,
                    time_s=np.array([0.0, 1.0]),
                    observables={"surface_depth": np.array([0.0, 0.001])},
                    metadata={
                        "observable_units": {"surface_depth": invalid_unit}
                    },
                )
                with self.assertRaises(DataValidationError):
                    series_from_result(result, "surface_depth")

        valid = synthetic_signal_result(
            backend=BackendMode.FORWARD,
            time_s=np.array([0.0, 1.0]),
            observables={"surface_depth": np.array([0.0, 0.001])},
            metadata={"observable_units": {"surface_depth": " m "}},
        )
        self.assertEqual(series_from_result(valid, "surface_depth").unit, "m")
        with self.assertRaises(DataValidationError):
            series_from_result(valid, "surface_depth", unit=" ")

    def test_piecewise_linear_error_integrals_are_exact(self) -> None:
        time = np.array([0.0, 1.0])
        zero = ObservableSeries(time, np.array([0.0, 0.0]), "m", "zero")

        ramp = ObservableSeries(time, np.array([0.0, 1.0]), "m", "ramp")
        ramp_metrics = compare_series(ramp, zero).metrics
        self.assertAlmostEqual(ramp_metrics.rmse, np.sqrt(1.0 / 3.0), places=15)
        self.assertAlmostEqual(ramp_metrics.mae, 0.5, places=15)

        crossing = ObservableSeries(
            time, np.array([-1.0, 1.0]), "m", "crossing"
        )
        crossing_metrics = compare_series(crossing, zero).metrics
        self.assertAlmostEqual(
            crossing_metrics.rmse, np.sqrt(1.0 / 3.0), places=15
        )
        self.assertAlmostEqual(crossing_metrics.mae, 0.5, places=15)

        right_rms = compare_series(zero, ramp, normalization="right_rms").metrics
        self.assertAlmostEqual(
            right_rms.normalization_scale, np.sqrt(1.0 / 3.0), places=15
        )
        self.assertAlmostEqual(right_rms.nrmse, 1.0, places=15)

        different_grid_ramp = ObservableSeries(
            np.array([0.0, 0.3, 1.0]),
            np.array([0.0, 0.3, 1.0]),
            "m",
            "different-grid-ramp",
        )
        different_grid_zero = ObservableSeries(
            np.array([0.0, 0.5, 1.0]),
            np.zeros(3),
            "m",
            "different-grid-zero",
        )
        different_grid = compare_series(
            different_grid_ramp, different_grid_zero
        ).metrics
        self.assertAlmostEqual(
            different_grid.rmse, np.sqrt(1.0 / 3.0), places=15
        )
        self.assertAlmostEqual(different_grid.mae, 0.5, places=15)

    def test_direct_threshold_error(self) -> None:
        error = compare_thresholds(102.0, 100.0, unit="rad/s")
        self.assertEqual(error.absolute_error, 2.0)
        self.assertEqual(error.relative_error, 0.02)


class ExperimentTests(unittest.TestCase):
    def test_csv_metadata_round_trip_preserves_si_values_and_role(self) -> None:
        original = make_dataset(CALIBRATION)
        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "run.csv"
            save_experiment(original, csv_path)
            loaded = load_experiment(csv_path)
        self.assertEqual(loaded.role, CALIBRATION)
        self.assertEqual(loaded.metadata["run_id"], "test-run")
        np.testing.assert_array_equal(loaded.time_s, original.time_s)
        np.testing.assert_array_equal(
            loaded.observables["surface_depth"],
            original.observables["surface_depth"],
        )

    def test_non_monotonic_time_and_missing_cells_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            metadata = {
                "schema_version": 1,
                "role": CALIBRATION,
                "columns": {
                    "t": {"canonical": "time", "unit": "s", "role": "time"},
                    "h": {
                        "canonical": "surface_depth",
                        "unit": "mm",
                        "role": "observable",
                    },
                },
            }
            (root / "bad.meta.json").write_text(json.dumps(metadata), encoding="utf-8")
            (root / "bad.csv").write_text("t,h\n0,1\n0,\n", encoding="utf-8")
            with self.assertRaisesRegex(DataValidationError, "missing"):
                load_experiment(root / "bad.csv")
            (root / "bad.csv").write_text("t,h\n1,1\n0,2\n", encoding="utf-8")
            with self.assertRaisesRegex(DataValidationError, "strictly increasing"):
                load_experiment(root / "bad.csv")

    def test_metadata_json_rejects_duplicate_keys_at_any_depth(self) -> None:
        columns = (
            '"columns":{"t":{"canonical":"time","unit":"s",'
            '"role":"time"},"h":{"canonical":"surface_depth",'
            '"unit":"mm","role":"observable"}}'
        )
        documents = (
            '{"schema_version":1,"schema_version":1,'
            f'"role":"{CALIBRATION}",{columns}}}',
            '{"schema_version":1,'
            f'"role":"{CALIBRATION}",'
            '"instrument":{"serial":"A","serial":"B"},'
            f'{columns}}}',
            '{"schema_version":1,'
            f'"role":"{CALIBRATION}",'
            '"columns":{"t":{"canonical":"time","canonical":"elapsed",'
            '"unit":"s","role":"time"},"h":{"canonical":'
            '"surface_depth","unit":"mm","role":"observable"}}}',
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "run.csv").write_text("t,h\n0,1\n", encoding="utf-8")
            for index, document in enumerate(documents):
                with self.subTest(index=index):
                    metadata_path = root / f"duplicate-{index}.json"
                    metadata_path.write_text(document, encoding="utf-8")
                    with self.assertRaisesRegex(DataValidationError, "duplicate"):
                        load_experiment(root / "run.csv", metadata_path)

    def test_metadata_json_rejects_nonstandard_and_trailing_tokens(self) -> None:
        columns = (
            '"columns":{"t":{"canonical":"time","unit":"s",'
            '"role":"time"},"h":{"canonical":"surface_depth",'
            '"unit":"mm","role":"observable"}}'
        )
        documents = (
            '{"schema_version":1,'
            f'"role":"{CALIBRATION}","nested":{{"value":NaN}},{columns}}}',
            '{"schema_version":1,'
            f'"role":"{CALIBRATION}","nested":[Infinity],{columns}}}',
            '{"schema_version":1,'
            f'"role":"{CALIBRATION}","nested":[-Infinity],{columns}}}',
            # A syntactically standard exponent can still overflow binary64.
            '{"schema_version":1,'
            f'"role":"{CALIBRATION}","nested":[1e9999],{columns}}}',
            '{"schema_version":1,'
            f'"role":"{CALIBRATION}",{columns}}} trailing',
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "run.csv").write_text("t,h\n0,1\n", encoding="utf-8")
            for index, document in enumerate(documents):
                with self.subTest(index=index):
                    metadata_path = root / f"nonfinite-{index}.json"
                    metadata_path.write_text(document, encoding="utf-8")
                    with self.assertRaises(DataValidationError):
                        load_experiment(root / "run.csv", metadata_path)

    def test_export_rejects_nested_nonfinite_metadata(self) -> None:
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as temp_dir:
                base = make_dataset(CALIBRATION)
                dataset = ExperimentDataset(
                    time_s=base.time_s,
                    observables=base.observables,
                    role=base.role,
                    columns=base.columns,
                    metadata={"nested": {"values": [1.0, value]}},
                )
                with self.assertRaisesRegex(DataValidationError, "strict JSON"):
                    save_experiment(dataset, Path(temp_dir) / "run.csv")

    def test_metadata_round_trip_is_standard_json(self) -> None:
        base = make_dataset(SEALED_VALIDATION)
        dataset = ExperimentDataset(
            time_s=base.time_s,
            observables=base.observables,
            role=base.role,
            columns=base.columns,
            metadata={
                "run_id": "盲测-甲",
                "nested": {"values": [1.25, 2, None, True], "note": "标准 JSON"},
            },
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "run.csv"
            _, metadata_path = save_experiment(dataset, csv_path)
            text = metadata_path.read_text(encoding="utf-8")
            decoded = json.loads(
                text,
                parse_constant=lambda token: self.fail(
                    f"non-standard constant emitted: {token}"
                ),
            )
            loaded = load_experiment(csv_path)

        self.assertEqual(decoded["nested"], dataset.metadata["nested"])
        self.assertIn("盲测-甲", text)
        self.assertNotIn("NaN", text)
        self.assertNotIn("Infinity", text)
        self.assertEqual(loaded.role, SEALED_VALIDATION)
        self.assertEqual(dict(loaded.metadata), dict(dataset.metadata))
        np.testing.assert_array_equal(loaded.time_s, dataset.time_s)
        np.testing.assert_array_equal(
            loaded.observables["surface_depth"],
            dataset.observables["surface_depth"],
        )

    def test_state_classification_reports_missing_columns(self) -> None:
        with self.assertRaisesRegex(
            DataValidationError, "connectivity_fraction"
        ):
            classify_experiment_window(
                make_dataset(CALIBRATION), detection_limit=1e-4
            )


class RoleGateTests(unittest.TestCase):
    def test_validation_stage_rejects_refit_by_default(self) -> None:
        calibration = make_dataset(CALIBRATION)
        sealed = make_dataset(SEALED_VALIDATION)
        guard = CalibrationGuard()
        fitted = guard.fit(calibration, lambda data: {"gain": 2.0})
        self.assertEqual(fitted["gain"], 2.0)
        guard.freeze()
        with self.assertRaises(RoleViolationError):
            guard.fit(calibration, lambda data: {"gain": 3.0})
        with self.assertRaises(RoleViolationError):
            guard.fit(sealed, lambda data: {"gain": 3.0}, allow_refit=True)
        prediction = guard.validate(
            sealed,
            lambda parameters, data: parameters["gain"]
            * data.observables["surface_depth"],
        )
        np.testing.assert_array_equal(prediction, [0.0, 0.002, 0.004])

    def test_demonstration_data_are_rejected_by_core_role_gates(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        demonstration = load_experiment(
            project_root / "examples" / "experiment_template.csv"
        )
        guard = CalibrationGuard()
        with self.assertRaisesRegex(RoleViolationError, "demonstration"):
            guard.fit(demonstration, lambda _data: {"gain": 1.0})

        sealed_base = make_dataset(SEALED_VALIDATION)
        sealed_demonstration = ExperimentDataset(
            time_s=sealed_base.time_s,
            observables=sealed_base.observables,
            role=sealed_base.role,
            columns=sealed_base.columns,
            metadata={
                "evidence_status": "DEMONSTRATION",
                "calibration_validation_eligible": False,
            },
        )
        guard.freeze({"gain": 1.0})
        with self.assertRaisesRegex(RoleViolationError, "demonstration"):
            guard.validate(
                sealed_demonstration,
                lambda parameters, _data: parameters["gain"],
            )

        malformed = ExperimentDataset(
            time_s=sealed_base.time_s,
            observables=sealed_base.observables,
            role=sealed_base.role,
            columns=sealed_base.columns,
            metadata={"calibration_validation_eligible": 1},
        )
        with self.assertRaisesRegex(RoleViolationError, "JSON boolean"):
            guard.validate(malformed, lambda _parameters, _data: None)


class SweepAndStateTests(unittest.TestCase):
    def test_sweep_job_ids_are_stable_and_two_dimensional(self) -> None:
        config = SimulationConfig()
        axes = (
            SweepAxis("drive.target_rpm", [400.0, 800.0]),
            SweepAxis("fluid.surface_tension_n_m", [0.04, 0.072]),
        )
        first = generate_sweep_plan(config, axes)
        second = generate_sweep_plan(config, axes)
        self.assertEqual(first.dimension, 2)
        self.assertEqual(len(first.jobs), 4)
        self.assertEqual(
            [job.job_id for job in first.jobs],
            [job.job_id for job in second.jobs],
        )
        self.assertEqual(len({job.job_id for job in first.jobs}), 4)
        self.assertEqual(first.jobs[-1].config_snapshot["drive"]["target_rpm"], 800.0)

    def test_s0_to_s4_operational_classification(self) -> None:
        self.assertEqual(
            classify_connectivity(
                depression_depth=0.0, detection_limit=1e-4
            ).state,
            ConnectivityState.S0,
        )
        self.assertEqual(
            classify_connectivity(
                depression_depth=1e-3, detection_limit=1e-4
            ).state,
            ConnectivityState.S1,
        )
        self.assertEqual(
            classify_connectivity(
                depression_depth=1e-3,
                detection_limit=1e-4,
                detached_bubbles=True,
            ).state,
            ConnectivityState.S2,
        )
        self.assertEqual(
            classify_connectivity(
                depression_depth=1e-3,
                detection_limit=1e-4,
                connectivity_fraction=0.96,
                observation_duration=20.0,
                angular_speed=2.0 * np.pi,
            ).state,
            ConnectivityState.S3,
        )
        self.assertEqual(
            classify_connectivity(
                depression_depth=1e-3,
                detection_limit=1e-4,
                unstable=True,
            ).state,
            ConnectivityState.S4,
        )

        invalid_slip = classify_connectivity(
            depression_depth=1e-3,
            detection_limit=1e-4,
            unstable=True,
            bar_desynchronized=True,
        )
        self.assertIsNone(invalid_slip.state)
        self.assertFalse(invalid_slip.valid_for_flow_state_statistics)
        self.assertEqual(invalid_slip.invalid_reason, "INVALID-SLIP")
        self.assertIn("excluded", invalid_slip.reason)

    def test_phase_aggregation_excludes_domain_limits_and_uses_current_schema(self) -> None:
        plan = generate_sweep_plan(
            SimulationConfig(),
            (SweepAxis("drive.target_rpm", [700.0, 900.0]),),
        )

        def result(*, end: float, reason: str, state: str) -> SimulationResult:
            time = np.array([0.0, end])
            return synthetic_signal_result(
                backend=BackendMode.FORWARD,
                time_s=time,
                observables={
                    "funnel_depth_m": np.array([0.0, 0.01]),
                    "surface_depth_m": np.array([0.0, 0.01]),
                    "min_gap_to_bar_m": np.array([0.0525, 0.0425]),
                    "critical_actual_rpm": np.array([1033.9, 1033.9]),
                },
                metadata={
                    "connectivity_state": state,
                    "observable_units": {
                        "funnel_depth_m": "m",
                        "surface_depth_m": "m",
                        "min_gap_to_bar_m": "m",
                        "critical_actual_rpm": "rpm",
                    },
                    "diagnostics": {"termination_reason": reason},
                },
                status=RunStatus.COMPLETED,
            )

        full = result(
            end=10.0,
            reason="configured_duration_reached",
            state="S1",
        )
        limited = result(
            end=9.0,
            reason="resolved_instantaneous_connection_candidate_domain_limit",
            state="S2",
        )
        points = aggregate_phase_data(
            plan,
            {
                plan.jobs[0].job_id: full,
                plan.jobs[1].job_id: limited,
            },
        )
        self.assertTrue(points[0].eligible_for_phase_map)
        self.assertEqual(points[0].state, ConnectivityState.S1)
        self.assertEqual(points[0].observables["funnel_depth_m"], 0.01)
        self.assertEqual(points[0].observable_units["funnel_depth_m"], "m")
        self.assertFalse(points[1].eligible_for_phase_map)
        self.assertIsNone(points[1].state)
        self.assertEqual(points[1].actual_end_s, 9.0)
        self.assertEqual(points[1].requested_end_s, 10.0)
        self.assertTrue(
            any("model-domain" in item for item in points[1].diagnostics)
        )

        invalid = aggregate_phase_data(
            plan,
            {plan.jobs[0].job_id: full},
            classifier=lambda _result: classify_connectivity(
                depression_depth=0.01,
                detection_limit=1.0e-4,
                bar_desynchronized=True,
            ),
        )[0]
        self.assertFalse(invalid.eligible_for_phase_map)
        self.assertIsNone(invalid.state)
        self.assertIn("INVALID-SLIP", invalid.diagnostics)

        no_state_metadata = dict(full.metadata)
        no_state_metadata.pop("connectivity_state")
        no_state = synthetic_signal_result(
            backend=full.backend,
            time_s=full.time_s,
            observables=full.observables,
            metadata=no_state_metadata,
            status=full.status,
        )
        unclassified = aggregate_phase_data(
            plan,
            {plan.jobs[0].job_id: no_state},
        )[0]
        self.assertFalse(unclassified.eligible_for_phase_map)
        self.assertIsNone(unclassified.state)
        self.assertTrue(
            any("connectivity state unavailable" in item for item in unclassified.diagnostics)
        )
        self.assertTrue(
            any("no valid S0--S4" in item for item in unclassified.diagnostics)
        )


if __name__ == "__main__":
    unittest.main()
