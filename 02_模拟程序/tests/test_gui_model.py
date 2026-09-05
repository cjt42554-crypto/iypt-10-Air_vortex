from __future__ import annotations

import json
import math
from pathlib import Path
import tempfile
import unittest

import numpy as np

from airvortex.core import BackendMode, PROBLEM_ID, RunStatus, SimulationConfig, SimulationResult
from airvortex.gui.model import (
    BACKEND_PRESENTATIONS,
    DEMONSTRATION_WARNING,
    DOMAIN_LIMIT_WARNING,
    ExperimentData,
    FORWARD_POST_CONTACT_WARNING,
    GuiViewModel,
    RunState,
    SignalSeries,
    compare_series,
    compute_hann_spectrum,
    normalize_simulation_result,
    read_experiment_csv,
)


from validity_fixtures import normalize_synthetic_signal_result


class GuiViewModelTests(unittest.TestCase):
    def test_default_runtime_config_is_core_config_with_exact_defaults(self) -> None:
        model = GuiViewModel()
        config = model.runtime_config()

        self.assertIsInstance(config, SimulationConfig)
        self.assertEqual(config.problem_id, PROBLEM_ID)
        self.assertIs(config.backend, BackendMode.FORWARD)
        self.assertAlmostEqual(config.container.radius_m, 0.045)
        self.assertAlmostEqual(config.container.liquid_depth_m, 0.060)
        self.assertAlmostEqual(config.fluid.liquid_dynamic_viscosity_pa_s, 0.001002)
        self.assertAlmostEqual(config.stir_bar.half_length_m, 0.015)
        self.assertAlmostEqual(config.stir_bar.radius_m, 0.003)
        self.assertAlmostEqual(config.drive.target_rpm, 900.0)
        self.assertEqual(
            BACKEND_PRESENTATIONS[BackendMode.FORWARD].badge,
            "DERIVED / CALIBRATION-REQUIRED",
        )
        self.assertEqual(
            BACKEND_PRESENTATIONS[BackendMode.HIGH_FIDELITY].badge,
            "3D LOCAL-LAW / VALIDATION-REQUIRED",
        )
        self.assertIn("不是生产级", BACKEND_PRESENTATIONS[BackendMode.HIGH_FIDELITY].warning)

    def test_cross_parameter_validation_is_actionable(self) -> None:
        model = GuiViewModel()
        model.set_parameter("liquid_depth_mm", "55")
        model.set_parameter("stirbar_clearance_mm", "50")
        _, issues = model.parsed_parameters()
        self.assertTrue(any(issue.key == "stirbar_clearance_mm" for issue in issues))
        with self.assertRaisesRegex(ValueError, "液面以下"):
            model.runtime_config()

    def test_fixed_burgers_decay_power_is_not_editable_or_changeable(self) -> None:
        model = GuiViewModel()
        visible = {item.key for item in model.specs_for_mode(BackendMode.FORWARD)}
        self.assertNotIn("azimuthal_decay_power", visible)
        model.set_parameter("azimuthal_decay_power", "0.8")
        with self.assertRaisesRegex(ValueError, "Burgers 衰减指数"):
            model.runtime_config()

        model.set_parameter("azimuthal_decay_power", "1")
        model.set_backend(BackendMode.HIGH_FIDELITY)
        hf_visible = {item.key for item in model.specs_for_mode()}
        self.assertIn("pressure_max_iterations", hf_visible)
        self.assertIn("hf_pressure_device", hf_visible)
        self.assertIn("grid_ntheta", hf_visible)
        model.set_parameter("hf_pressure_device", "CPU")
        self.assertEqual(
            model.runtime_config().high_fidelity.pressure_solver_device, "cpu"
        )
        model.set_parameter("hf_pressure_device", "invalid")
        with self.assertRaisesRegex(ValueError, "auto、cuda 或 cpu"):
            model.runtime_config()
        model.set_parameter("hf_pressure_device", "auto")
        model.set_parameter("pressure_max_iterations", "100")
        self.assertEqual(model.runtime_config().high_fidelity.pressure_max_iterations, 100)
        model.set_parameter("grid_ntheta", "32")
        self.assertEqual(model.runtime_config().high_fidelity.azimuthal_cells, 32)

    def test_gui_preserves_signed_piecewise_actual_speed_schedule(self) -> None:
        model = GuiViewModel()
        model.set_parameter("duration_s", "1")
        model.set_parameter("actual_speed_schedule_time_s", "0, 0.2, 0.5, 1")
        model.set_parameter("actual_speed_schedule_rpm", "0, 900, -900, 0")
        config = model.runtime_config()
        self.assertEqual(
            config.drive.actual_speed_schedule_time_s, (0.0, 0.2, 0.5, 1.0)
        )
        self.assertEqual(
            config.drive.actual_speed_schedule_rpm, (0.0, 900.0, -900.0, 0.0)
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "schedule.json"
            model.save_config(path)
            restored = GuiViewModel()
            restored.load_config(path)
            self.assertEqual(restored.runtime_config(), config)
        with self.assertRaisesRegex(ValueError, "不是可扫描"):
            model.scan_values("actual_speed_schedule_rpm", "0", "1", "2")

    def test_run_state_policy_locks_physics_and_enforces_transitions(self) -> None:
        model = GuiViewModel()
        self.assertTrue(model.button_policy().physical_controls)
        model.set_run_state(RunState.RUNNING)
        self.assertFalse(model.button_policy().physical_controls)
        self.assertTrue(model.button_policy().pause)
        with self.assertRaisesRegex(RuntimeError, "已锁定"):
            model.set_parameter("rpm", "800")
        with self.assertRaisesRegex(RuntimeError, "非法运行状态转换"):
            model.set_run_state(RunState.IDLE)
        model.set_run_state(RunState.PAUSED)
        self.assertTrue(model.button_policy().resume)
        model.set_run_state(RunState.RUNNING)
        model.set_run_state(RunState.COMPLETED)
        self.assertTrue(model.button_policy().start)
        model.set_run_state(RunState.IDLE)
        model.set_run_state(RunState.RUNNING)
        model.set_run_state(RunState.STOPPING)
        model.set_run_state(RunState.CANCELLED)
        self.assertTrue(model.button_policy().start)
        model.set_run_state(RunState.IDLE)
        model.set_run_state(RunState.RUNNING)
        model.set_run_state(RunState.DOMAIN_LIMIT)
        self.assertTrue(model.button_policy().start)

    def test_core_config_round_trip_and_strict_unknown_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "case.json"
            model = GuiViewModel()
            model.set_backend(BackendMode.HIGH_FIDELITY)
            model.set_parameter("rpm", "875")
            model.save_config(path)

            restored = GuiViewModel()
            unknown = restored.load_config(path)
            self.assertIs(restored.backend_mode, BackendMode.HIGH_FIDELITY)
            self.assertEqual(restored.parameters["rpm"], "875")
            self.assertEqual(unknown, ())

            document = json.loads(path.read_text(encoding="utf-8"))
            document["container"]["future_parameter"] = 3
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unknown field"):
                restored.load_config(path)

    def test_hf_quick_preset_has_bounded_work_and_valid_core_config(self) -> None:
        model = GuiViewModel()
        model.set_backend(BackendMode.HIGH_FIDELITY)
        estimate = model.apply_hf_quick_preset()
        config = model.runtime_config()
        self.assertEqual(config.high_fidelity.radial_cells, 12)
        self.assertEqual(config.high_fidelity.azimuthal_cells, 12)
        self.assertEqual(config.high_fidelity.axial_cells, 16)
        self.assertAlmostEqual(config.run.duration_s, 0.005)
        self.assertEqual(math.ceil(config.run.duration_s / config.high_fidelity.time_step_s), 25)
        self.assertIn("名义工作量下界", estimate)
        self.assertIn("自适应稳定性限制可能增加步数", estimate)
        self.assertIn("57,600 单元步", estimate)
        self.assertIn("12×12×16", estimate)
        self.assertIn("快速预览", estimate)

    def test_experiment_csv_quick_import_normalizes_units_and_marks_role(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "experiment.csv"
            path.write_text(
                "time_ms,气芯深度(mm),rpm\n0,0,600\n100,2.5,620\n250,5.0,650\n",
                encoding="utf-8",
            )
            data = read_experiment_csv(path)

        self.assertEqual(data.time_s, (0.0, 0.1, 0.25))
        self.assertEqual(data.role, "unassigned_quick_import")
        self.assertIn("warning", data.metadata)
        for actual, expected in zip(
            data.signals["surface_depth_m"].values, (0.0, 0.0025, 0.005)
        ):
            self.assertAlmostEqual(actual, expected)
        self.assertEqual(data.signals["actual_bar_rpm"].unit, "r/min")
        explicit_view = normalize_simulation_result(
            {
                "time_s": data.time_s,
                "signals": {
                    "surface_depth_m": {
                        "values": (0.0, 0.0025, 0.005),
                        "unit": "m",
                    }
                },
            },
            BackendMode.FORWARD,
        )
        with self.assertRaisesRegex(ValueError, "只可叠图"):
            compare_series(data, explicit_view, "surface_depth_m")

    def test_experiment_csv_requires_strict_time(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "bad.csv"
            path.write_text("time_s,core_depth_m\n0,0\n0,0.1\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "严格递增"):
                read_experiment_csv(path)

    def test_shipped_formal_experiment_maps_si_canonical_and_display_rpm(self) -> None:
        source = Path(__file__).resolve().parents[1] / "examples" / "experiment_template.csv"
        data = read_experiment_csv(source)

        self.assertEqual(data.role, "calibration")
        self.assertEqual(data.signals["surface_depth_m"].unit, "m")
        self.assertEqual(data.signals["min_gap_to_bar_m"].unit, "m")
        self.assertEqual(data.signals["actual_angular_velocity_rad_s"].unit, "rad/s")
        self.assertEqual(data.signals["actual_bar_rpm"].unit, "r/min")
        self.assertAlmostEqual(data.signals["surface_depth_m"].values[-1], 0.0218)
        self.assertAlmostEqual(data.signals["min_gap_to_bar_m"].values[0], 0.0525)
        self.assertAlmostEqual(
            data.signals["actual_angular_velocity_rad_s"].values[-1],
            900.0 * 2.0 * math.pi / 60.0,
        )
        self.assertAlmostEqual(data.signals["actual_bar_rpm"].values[-1], 900.0)
        self.assertEqual(
            data.metadata["gui_formal_adapter"]["contract"],
            "analysis-canonical-SI-to-gui/v1",
        )
        self.assertIn(DEMONSTRATION_WARNING, data.metadata["warning"])
        self.assertIs(data.metadata["calibration_validation_eligible"], False)

        matching = normalize_synthetic_signal_result(
            {
                "time_s": data.time_s,
                "signals": {
                    "surface_depth_m": {
                        "values": data.signals["surface_depth_m"].values,
                        "unit": "m",
                    },
                    "min_gap_to_bar_m": {
                        "values": data.signals["min_gap_to_bar_m"].values,
                        "unit": "m",
                    },
                    "actual_bar_rpm": {
                        "values": data.signals["actual_bar_rpm"].values,
                        "unit": "rpm",
                    },
                },
            },
            BackendMode.FORWARD,
        )
        for key in ("surface_depth_m", "min_gap_to_bar_m", "actual_bar_rpm"):
            metrics = compare_series(data, matching, key)
            self.assertEqual(metrics.samples, 3)
            self.assertAlmostEqual(metrics.rmse, 0.0)

    def test_formal_experiment_rejects_known_canonical_with_wrong_dimension(self) -> None:
        examples = Path(__file__).resolve().parents[1] / "examples"
        with tempfile.TemporaryDirectory() as temporary:
            csv_path = Path(temporary) / "experiment.csv"
            metadata_path = Path(temporary) / "experiment.meta.json"
            csv_path.write_text(
                (examples / "experiment_template.csv").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            metadata = json.loads(
                (examples / "experiment_template.meta.json").read_text(encoding="utf-8")
            )
            metadata["columns"]["surface_depth_mm"]["unit"] = "rpm"
            metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "同名异维"):
                read_experiment_csv(csv_path)

    def test_formal_bar_speed_restores_signed_direction_or_fails_closed(self) -> None:
        examples = Path(__file__).resolve().parents[1] / "examples"
        source_csv = examples / "experiment_template.csv"
        source_metadata = examples / "experiment_template.meta.json"

        with tempfile.TemporaryDirectory() as temporary:
            csv_path = Path(temporary) / "reverse.csv"
            metadata_path = Path(temporary) / "reverse.meta.json"
            csv_path.write_text(source_csv.read_text(encoding="utf-8"), encoding="utf-8")
            metadata = json.loads(source_metadata.read_text(encoding="utf-8"))
            metadata["rotation_direction"] = -1
            metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

            data = read_experiment_csv(csv_path)
            signed_omega = data.signals["actual_angular_velocity_rad_s"]
            signed_rpm = data.signals["actual_bar_rpm"]
            self.assertTrue(all(item <= 0.0 for item in signed_omega.values))
            self.assertAlmostEqual(signed_omega.values[-1], -900.0 * 2.0 * math.pi / 60.0)
            self.assertAlmostEqual(signed_rpm.values[-1], -900.0)
            self.assertEqual(
                data.metadata["gui_formal_adapter"]["mappings"]["bar_speed"][
                    "rotation_direction"
                ],
                -1,
            )

            matching = normalize_synthetic_signal_result(
                {
                    "time_s": data.time_s,
                    "signals": {
                        "actual_angular_velocity_rad_s": {
                            "values": signed_omega.values,
                            "unit": "rad/s",
                        },
                        "actual_bar_rpm": {
                            "values": signed_rpm.values,
                            "unit": "r/min",
                        },
                    },
                },
                BackendMode.FORWARD,
            )
            for key in ("actual_angular_velocity_rad_s", "actual_bar_rpm"):
                self.assertAlmostEqual(compare_series(data, matching, key).rmse, 0.0)

        invalid_directions: tuple[object, ...] = (None, 0, 2, True, 1.0, "-1")
        for direction in invalid_directions:
            with self.subTest(direction=direction), tempfile.TemporaryDirectory() as temporary:
                csv_path = Path(temporary) / "invalid.csv"
                metadata_path = Path(temporary) / "invalid.meta.json"
                csv_path.write_text(
                    source_csv.read_text(encoding="utf-8"), encoding="utf-8"
                )
                metadata = json.loads(source_metadata.read_text(encoding="utf-8"))
                if direction is None:
                    metadata.pop("rotation_direction", None)
                else:
                    metadata["rotation_direction"] = direction
                metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "rotation_direction"):
                    read_experiment_csv(csv_path)

    def test_formal_demonstration_flags_block_evidence_claims(self) -> None:
        examples = Path(__file__).resolve().parents[1] / "examples"
        for flag in (
            {"evidence_status": "demonstration"},
            {"is_demonstration": True},
        ):
            with self.subTest(flag=flag), tempfile.TemporaryDirectory() as temporary:
                csv_path = Path(temporary) / "experiment.csv"
                metadata_path = Path(temporary) / "experiment.meta.json"
                csv_path.write_text(
                    (examples / "experiment_template.csv").read_text(encoding="utf-8"),
                    encoding="utf-8",
                )
                metadata = json.loads(
                    (examples / "experiment_template.meta.json").read_text(
                        encoding="utf-8"
                    )
                )
                metadata.update(flag)
                metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
                data = read_experiment_csv(csv_path)
                self.assertEqual(data.role, "calibration")
                self.assertIn(DEMONSTRATION_WARNING, data.metadata["warning"])
                self.assertIs(data.metadata["calibration_validation_eligible"], False)
                model = GuiViewModel()
                model.experiment = data
                model.accept_result(
                    SimulationResult(
                        backend=BackendMode.FORWARD,
                        time_s=np.array([0.0, 0.1]),
                        observables={"surface_depth_m": np.array([0.0, 0.001])},
                        metadata={"observable_units": {"surface_depth_m": "m"}},
                    ),
                    config=model.runtime_config(),
                )
                _csv, exported_metadata = model.export_result_bundle(
                    Path(temporary) / "result.csv"
                )
                exported = json.loads(exported_metadata.read_text(encoding="utf-8"))
                self.assertIn(
                    DEMONSTRATION_WARNING,
                    exported["experiment_overlay"]["metadata"]["warning"],
                )

    def test_real_forward_simulation_result_normalization(self) -> None:
        result = SimulationResult(
            backend=BackendMode.FORWARD,
            time_s=np.array([0.0, 0.5]),
            observables={
                "surface_depth_m": np.array([0.0, 0.01]),
                "surface_profile_m": np.array([[0.06, 0.061], [0.05, 0.061]]),
                "burgers_C_m2_s": np.array([0.0, 1e-4]),
                "physical_circulation_gamma_m2_s": np.array([0.0, 6.28e-4]),
            },
            fields={"radius_m": np.array([0.0, 0.045])},
            metadata={
                "observable_units": {
                    "surface_depth_m": "m",
                    "burgers_C_m2_s": "m²·s⁻¹",
                    "physical_circulation_gamma_m2_s": "m²·s⁻¹",
                },
                "diagnostics": {"radial_points": 2},
            },
            status=RunStatus.COMPLETED,
        )
        view = normalize_simulation_result(result, BackendMode.FORWARD)

        self.assertIsNotNone(view.signal("surface_depth_m"))
        self.assertEqual(view.signals["burgers_c_m2_s"].label, "Burgers 系数 C")
        self.assertEqual(
            view.signals["physical_circulation_gamma_m2_s"].label,
            "远场物理环量 Γ",
        )
        self.assertEqual(len(view.surface_frames), 2)
        self.assertAlmostEqual(view.surface_frames[1].height_m[0], 0.05)
        self.assertEqual(view.diagnostics["radial_points"], 2)

    def test_surface_profiles_use_result_times_not_single_final_field_time(self) -> None:
        result = SimulationResult(
            backend=BackendMode.FORWARD,
            time_s=np.array([0.0, 1.0, 2.0]),
            observables={
                "surface_depth_m": np.array([0.0, 0.011, 0.022]),
                "surface_profile_m": np.array(
                    [
                        [0.060, 0.060],
                        [0.050, 0.061],
                        [0.040, 0.062],
                    ]
                ),
                "surface_profile_minimum_m": np.array(
                    [[0.059, 0.060], [0.049, 0.060], [0.039, 0.061]]
                ),
                "surface_profile_maximum_m": np.array(
                    [[0.061, 0.061], [0.051, 0.062], [0.041, 0.063]]
                ),
            },
            fields={
                "r_m": np.array([0.0, 0.045]),
                # F1.0 archives one final field frame but a surface profile at
                # every result time.  This singleton must not timestamp all
                # three surface profiles as t=2 s.
                "frame_time_s": np.array([2.0]),
                "phase_liquid_fraction": np.ones((2, 2)),
            },
            metadata={
                "observable_units": {
                    "surface_depth_m": "m",
                    "surface_profile_m": "m",
                    "surface_profile_minimum_m": "m",
                    "surface_profile_maximum_m": "m",
                }
            },
            status=RunStatus.COMPLETED,
        )

        view = normalize_simulation_result(result, BackendMode.FORWARD)

        self.assertEqual(
            [frame.time_s for frame in view.surface_frames],
            [0.0, 1.0, 2.0],
        )
        self.assertEqual(
            [frame.height_m[0] for frame in view.surface_frames],
            [0.060, 0.050, 0.040],
        )
        self.assertEqual(
            [frame.minimum_height_m[0] for frame in view.surface_frames],
            [0.059, 0.049, 0.039],
        )
        self.assertEqual(
            [frame.maximum_height_m[-1] for frame in view.surface_frames],
            [0.061, 0.062, 0.063],
        )
        self.assertEqual(
            [frame.time_s for frame in view.field_frames],
            [2.0],
        )

    def test_real_forward_post_contact_is_extrapolation_not_early_termination(self) -> None:
        from airvortex.backends.forward import ForwardTheoryBackend

        document = SimulationConfig().to_dict()
        document["drive"]["target_rpm"] = 1500.0
        document["run"]["duration_s"] = 3.0
        document["run"]["output_interval_s"] = 0.1
        config = SimulationConfig.from_dict(document)
        result = ForwardTheoryBackend().run(config)
        self.assertEqual(result.metadata["domain_status"], "OUT_OF_MODEL_DOMAIN")
        self.assertAlmostEqual(float(result.time_s[-1]), config.run.duration_s)

        view = normalize_simulation_result(result, BackendMode.FORWARD)
        self.assertIn(FORWARD_POST_CONTACT_WARNING, view.warnings)
        self.assertFalse(view.domain_limited)

        model = GuiViewModel()
        model.begin_result_batch()
        model.accept_result(result, BackendMode.FORWARD, config=config)
        self.assertEqual(model.progress, 1.0)
        self.assertFalse(model.last_job_hit_domain_limit())
        self.assertIn(FORWARD_POST_CONTACT_WARNING, model.progress_message)

        hf_config = SimulationConfig(backend=BackendMode.HIGH_FIDELITY)
        model.accept_result(
            SimulationResult(
                backend=BackendMode.HIGH_FIDELITY,
                time_s=np.array([0.0, 0.1]),
                observables={"surface_depth_m": np.array([0.0, 0.0])},
                metadata={"observable_units": {"surface_depth_m": "m"}},
            ),
            BackendMode.HIGH_FIDELITY,
            config=hf_config,
        )
        self.assertTrue(
            any(FORWARD_POST_CONTACT_WARNING in item for item in model.last_job_warnings)
        )

        model.begin_result_batch()
        scan = model.accept_scan_results(
            mode=BackendMode.FORWARD,
            parameter_key="rpm",
            input_values=(1500.0,),
            results=(result,),
            plan_hash="forward-contact",
            planned_count=1,
        )
        self.assertIn(
            FORWARD_POST_CONTACT_WARNING, scan.points[0].termination_annotation
        )

        with tempfile.TemporaryDirectory() as temporary:
            model.accept_result(result, BackendMode.FORWARD, config=config)
            _csv, metadata_path = model.export_result_bundle(
                Path(temporary) / "forward.csv", BackendMode.FORWARD
            )
            exported = json.loads(metadata_path.read_text(encoding="utf-8"))
        self.assertIn(FORWARD_POST_CONTACT_WARNING, exported["result_warnings"])

    def test_real_hf_simulation_result_normalizes_fields_and_velocity(self) -> None:
        result = SimulationResult(
            backend=BackendMode.HIGH_FIDELITY,
            time_s=np.array([0.0]),
            observables={
                "surface_profile_m": np.array([[0.05, 0.06]]),
                "surface_depth": np.array([0.01]),
            },
            fields={
                "r_m": np.array([0.01, 0.03]),
                "z_m": np.array([0.01, 0.03, 0.05]),
                "frame_time_s": np.array([0.0]),
                "phase_liquid_fraction": np.array(
                    [[[1.0, 0.5, 0.0], [1.0, 1.0, 0.0]]]
                ),
                "u_r_m_s": np.ones((1, 2, 3)) * 0.3,
                "u_z_m_s": np.ones((1, 2, 3)) * 0.4,
                "u_theta_m_s": np.zeros((1, 2, 3)),
            },
            metadata={"diagnostics": {"azimuthal_cells_used": False}},
        )
        view = normalize_simulation_result(result, BackendMode.HIGH_FIDELITY)

        self.assertEqual(len(view.field_frames), 1)
        field = view.field_frames[0]
        self.assertEqual(len(field.phase or ()), 3)  # (r,z) -> GUI (z,r)
        self.assertEqual(len((field.phase or ())[0]), 2)
        self.assertAlmostEqual((field.speed_m_s or ((0.0,),))[0][0], 0.5)
        self.assertEqual(field.extent_m, (0.0, 0.03, 0.0, 0.05))

    def test_domain_limit_is_prominent_for_single_result_and_scan(self) -> None:
        result = SimulationResult(
            backend=BackendMode.HIGH_FIDELITY,
            time_s=np.array([0.0, 0.1]),
            observables={"surface_depth_m": np.array([0.0, 0.002])},
            metadata={
                "observable_units": {"surface_depth_m": "m"},
                "diagnostics": {
                    "termination_reason": (
                        "resolved_instantaneous_connection_candidate_domain_limit"
                    )
                },
            },
            status=RunStatus.COMPLETED,
        )
        view = normalize_simulation_result(result, BackendMode.HIGH_FIDELITY)
        self.assertTrue(view.domain_limited)
        self.assertIn(DOMAIN_LIMIT_WARNING, view.warnings)
        self.assertTrue(view.diagnostics["domain_limit_early_termination"])

        model = GuiViewModel()
        model.set_backend(BackendMode.HIGH_FIDELITY)
        model.begin_result_batch()
        model.accept_result(result, config=model.runtime_config())
        self.assertIn(DOMAIN_LIMIT_WARNING, model.progress_message)
        self.assertLess(model.progress, 1.0)
        self.assertTrue(any(DOMAIN_LIMIT_WARNING in item for item in model.last_job_warnings))

        scan = model.accept_scan_results(
            mode=BackendMode.HIGH_FIDELITY,
            parameter_key="rpm",
            input_values=(900.0,),
            results=(result,),
            plan_hash="test-plan",
            planned_count=1,
        )
        self.assertIn(DOMAIN_LIMIT_WARNING, scan.points[0].termination_annotation)
        self.assertTrue(any(DOMAIN_LIMIT_WARNING in item for item in scan.diagnostics))
        self.assertIn(DOMAIN_LIMIT_WARNING, str(model.diagnostics["scan.diagnostics"]))

    def test_hann_spectrum_finds_known_sine_and_refuses_invalid_sampling(self) -> None:
        sample_rate_hz = 64.0
        times = np.arange(128, dtype=float) / sample_rate_hz
        values = 3.0 + 1.7 * np.sin(2.0 * np.pi * 5.0 * times)
        signal = SignalSeries("probe", "探针信号", "m", tuple(values))
        spectrum = compute_hann_spectrum(tuple(times), signal)

        self.assertTrue(spectrum.available)
        peak_index = int(np.argmax(np.asarray(spectrum.amplitudes)[1:])) + 1
        self.assertAlmostEqual(spectrum.frequencies_hz[peak_index], 5.0)
        self.assertAlmostEqual(spectrum.amplitudes[peak_index], 1.7, delta=0.03)
        self.assertEqual(spectrum.amplitude_unit, "m")
        self.assertEqual(spectrum.metadata["interpolation"], "none")
        self.assertIn("periodic", str(spectrum.metadata["window"]))
        self.assertIn("不能作为稳态/稳定性证据", spectrum.warning)

        irregular_times = list(times)
        irregular_times[40] += 0.001
        irregular = compute_hann_spectrum(irregular_times, signal)
        self.assertFalse(irregular.available)
        self.assertIn("不会自动插值", irregular.reason)

        missing = SignalSeries(
            "probe", "探针信号", "m", tuple([*values[:-1], math.nan])
        )
        self.assertIn("不插值", compute_hann_spectrum(tuple(times), missing).reason)
        short = SignalSeries("probe", "探针信号", "m", tuple(values[:8]))
        self.assertIn("记录过短", compute_hann_spectrum(tuple(times[:8]), short).reason)

    def test_compare_series_calls_analysis_alignment(self) -> None:
        reference = normalize_synthetic_signal_result(
            {
                "time_s": [0.0, 0.5, 1.0],
                "signals": {
                    "surface_depth_m": {"values": [0.0, 0.5, 1.0], "unit": "m"}
                },
            },
            BackendMode.FORWARD,
        )
        candidate = normalize_synthetic_signal_result(
            {
                "time_s": [0.0, 1.0],
                "signals": {
                    "surface_depth_m": {"values": [0.0, 1.2], "unit": "m"}
                },
            },
            BackendMode.HIGH_FIDELITY,
        )
        metrics = compare_series(reference, candidate, "surface_depth_m")
        self.assertEqual(metrics.samples, 3)
        self.assertAlmostEqual(metrics.bias, 0.1)
        self.assertAlmostEqual(metrics.mae, 0.1)
        self.assertAlmostEqual(
            metrics.rmse,
            math.sqrt(0.04 / 3.0),  # 分段线性残差 0.2t 的精确时间积分
        )

    def test_compare_rejects_unit_mismatch(self) -> None:
        left = normalize_simulation_result(
            {"time_s": [0.0, 1.0], "signals": {"x": {"values": [0, 1], "unit": "m"}}},
            BackendMode.FORWARD,
        )
        right = normalize_simulation_result(
            {"time_s": [0.0, 1.0], "signals": {"x": {"values": [0, 1], "unit": "mm"}}},
            BackendMode.HIGH_FIDELITY,
        )
        with self.assertRaisesRegex(ValueError, "单位不一致"):
            compare_series(left, right, "x")

    def test_compare_rejects_missing_invalid_and_placeholder_units(self) -> None:
        def view(raw_signal: object, mode: BackendMode):
            return normalize_simulation_result(
                {"time_s": [0.0, 1.0], "signals": {"x": raw_signal}}, mode
            )

        valid = view({"values": [0.0, 1.0], "unit": "m"}, BackendMode.HIGH_FIDELITY)
        bad_views = (
            view({"values": [0.0, 1.0]}, BackendMode.FORWARD),
            view({"values": [0.0, 1.0], "unit": None}, BackendMode.FORWARD),
            view({"values": [0.0, 1.0], "unit": 7}, BackendMode.FORWARD),
            view({"values": [0.0, 1.0], "unit": "SI"}, BackendMode.FORWARD),
        )
        for bad in bad_views:
            with self.subTest(unit=bad.signals["x"].unit):
                with self.assertRaisesRegex(ValueError, "单位"):
                    compare_series(bad, valid, "x")

        missing_both = bad_views[0]
        with self.assertRaisesRegex(ValueError, "未显式声明"):
            compare_series(missing_both, missing_both, "x")
        with self.assertRaisesRegex(ValueError, "占位符"):
            compare_series(bad_views[3], bad_views[3], "x")
        direct_numeric = ExperimentData(
            Path("numeric-unit.csv"),
            (0.0, 1.0),
            {"x": SignalSeries("x", "x", 3, (0.0, 1.0))},  # type: ignore[arg-type]
            ("time_s", "x"),
            role="calibration",
        )
        with self.assertRaisesRegex(ValueError, "明确字符串"):
            compare_series(direct_numeric, direct_numeric, "x")

        known_default = normalize_simulation_result(
            {"time_s": [0.0, 1.0], "surface_depth_m": [0.0, 0.001]},
            BackendMode.FORWARD,
        )
        self.assertEqual(known_default.signals["surface_depth_m"].unit, "m")
        self.assertEqual(
            known_default.signals["surface_depth_m"].unit_origin,
            "inferred_from_canonical",
        )
        known_explicit = normalize_simulation_result(
            {
                "time_s": [0.0, 1.0],
                "signals": {
                    "surface_depth_m": {"values": [0.0, 0.001], "unit": "m"}
                },
            },
            BackendMode.HIGH_FIDELITY,
        )
        with self.assertRaisesRegex(ValueError, "未显式声明"):
            compare_series(known_default, known_explicit, "surface_depth_m")

        known_observable_without_units = normalize_simulation_result(
            {
                "time_s": [0.0, 1.0],
                "observables": {"surface_depth_m": [0.0, 0.001]},
                "metadata": {},
            },
            BackendMode.FORWARD,
        )
        self.assertEqual(
            known_observable_without_units.signals[
                "surface_depth_m"
            ].unit_origin,
            "inferred_from_canonical",
        )
        with self.assertRaisesRegex(ValueError, "未显式声明"):
            compare_series(
                known_observable_without_units,
                known_explicit,
                "surface_depth_m",
            )

    def test_compare_rejects_missing_samples_without_cross_gap_filling(self) -> None:
        cases = (
            ((0.0, math.nan, 1.0), (0.0, 0.5, 1.0), "参考 2/3"),
            ((math.nan, 0.5, 1.0), (0.0, 0.5, 1.0), "参考 2/3"),
            ((0.0, 0.5, 1.0), (0.0, 0.5, math.nan), "候选 2/3"),
        )
        for reference_values, candidate_values, count_text in cases:
            with self.subTest(
                reference=reference_values, candidate=candidate_values
            ):
                reference = ExperimentData(
                    Path("reference.csv"),
                    (0.0, 0.5, 1.0),
                    {"x": SignalSeries("x", "x", "m", reference_values)},
                    ("time_s", "x"),
                    role="calibration",
                )
                candidate = ExperimentData(
                    Path("candidate.csv"),
                    (0.0, 0.5, 1.0),
                    {"x": SignalSeries("x", "x", "m", candidate_values)},
                    ("time_s", "x"),
                    role="calibration",
                )
                with self.assertRaisesRegex(ValueError, "不跨缺口插值") as caught:
                    compare_series(reference, candidate, "x")
                self.assertIn(count_text, str(caught.exception))

    def test_scan_generation_and_integer_uniqueness(self) -> None:
        model = GuiViewModel()
        self.assertEqual(model.scan_values("rpm", 100, 500, 5), (100, 200, 300, 400, 500))
        log_values = model.scan_values("liquid_viscosity_mpas", 1, 100, 3, logarithmic=True)
        for actual, expected in zip(log_values, (1, 10, 100)):
            self.assertAlmostEqual(actual, expected)
        with self.assertRaisesRegex(ValueError, "互异"):
            model.scan_values("grid_nr", 20, 21, 4)

    def test_export_always_writes_evidence_and_exact_config_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            model = GuiViewModel()
            config = model.runtime_config()
            model.accept_result(
                SimulationResult(
                    backend=BackendMode.FORWARD,
                    time_s=np.array([0.0, 0.1]),
                    observables={"surface_depth_m": np.array([0.0, 0.002])},
                    metadata={"backend_version": "test", "observable_units": {"surface_depth_m": "m"}},
                ),
                config=config,
            )
            csv_path, metadata_path = model.export_result_bundle(
                Path(temporary) / "result.csv"
            )
            self.assertTrue(csv_path.exists())
            self.assertTrue(metadata_path.exists())
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))

        self.assertEqual(metadata["evidence_badge"], "DERIVED / CALIBRATION-REQUIRED")
        self.assertEqual(metadata["result_metadata"]["backend_version"], "test")
        self.assertEqual(metadata["runtime_manifest_status"], "not_supplied")
        self.assertEqual(metadata["config"]["problem_id"], PROBLEM_ID)
        self.assertAlmostEqual(metadata["config"]["drive"]["target_rpm"], 900.0)


if __name__ == "__main__":
    unittest.main()
