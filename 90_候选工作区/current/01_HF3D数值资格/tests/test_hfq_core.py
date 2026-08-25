from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import hashlib
from io import StringIO
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hfq_core import (  # noqa: E402
    SeriesData,
    audit_refinement_design,
    audit_config,
    convergence_estimate,
    dimensions_for_target_bar_resolution,
    event_consistency,
    event_summary,
    generate_campaign,
    grid_audit,
    physical_signature,
    solve_observed_order,
)
from hfq_cli_run import _run_command  # noqa: E402
from hfq_cli_common import (  # noqa: E402
    LoadedCase,
    _effective_time_step_gate,
    _explicit_config_differences,
    _quality_gate,
)
from hfq_cli_analysis import _analyse_command, _terminal_failure_record  # noqa: E402


def base_config() -> dict:
    return {
        "schema_version": 2,
        "problem_id": "iypt-2027-10-air-vortex",
        "backend": "high_fidelity",
        "container": {
            "radius_m": 0.045,
            "liquid_depth_m": 0.06,
            "headspace_height_m": 0.02,
        },
        "stir_bar": {
            "half_length_m": 0.015,
            "radius_m": 0.003,
            "bottom_clearance_m": 0.0015,
        },
        "drive": {
            "initial_rpm": 0.0,
            "target_rpm": 300.0,
            "ramp_duration_s": 0.002,
            "rotation_direction": 1,
            "actual_speed_schedule_time_s": [],
            "actual_speed_schedule_rpm": [],
        },
        "fluid": {
            "liquid_density_kg_m3": 998.2,
            "gas_density_kg_m3": 1.204,
            "surface_tension_n_m": 0.072,
        },
        "run": {"duration_s": 0.005, "output_interval_s": 0.001},
        "high_fidelity": {
            "radial_cells": 12,
            "azimuthal_cells": 12,
            "axial_cells": 16,
            "time_step_s": 0.0002,
            "interface_thickness_cells": 2.5,
            "boundary_layer_cells": 1.5,
        },
    }


class GridAuditTests(unittest.TestCase):
    def test_quick_preview_uses_less_than_one_cell_on_worst_axis(self) -> None:
        audit = grid_audit(base_config())
        self.assertAlmostEqual(audit.dx_m, 0.0075)
        self.assertAlmostEqual(audit.dz_m, 0.005)
        self.assertAlmostEqual(audit.bar_diameter_cells_best_axis, 1.2)
        self.assertAlmostEqual(audit.bar_diameter_cells_worst_axis, 0.8)
        self.assertAlmostEqual(audit.aspect_ratio, 1.5)

    def test_target_dimensions_meet_worst_axis_resolution(self) -> None:
        config = base_config()
        nx, ny, nz = dimensions_for_target_bar_resolution(
            config, 9.0, round_to_multiple=2
        )
        self.assertEqual((nx, ny, nz), (136, 136, 120))
        config["high_fidelity"].update(
            radial_cells=nx, azimuthal_cells=ny, axial_cells=nz
        )
        self.assertGreaterEqual(grid_audit(config).bar_diameter_cells_worst_axis, 9.0)

    def test_preview_audit_is_fail_closed(self) -> None:
        result = audit_config(base_config(), label="test")
        self.assertEqual(result["claim_ceiling"], "PREVIEW_ONLY")
        codes = {item["code"] for item in result["warnings"]}
        self.assertIn("BAR_SUBCELL_WORST_AXIS", codes)
        self.assertFalse(result["apparatus_prediction_allowed"])


class SignatureTests(unittest.TestCase):
    def test_only_discretization_controls_are_removed(self) -> None:
        left = base_config()
        right = json.loads(json.dumps(left))
        right["high_fidelity"]["radial_cells"] = 120
        right["high_fidelity"]["time_step_s"] = 1.0e-5
        self.assertEqual(physical_signature(left), physical_signature(right))
        right["drive"]["target_rpm"] = 400.0
        self.assertNotEqual(physical_signature(left), physical_signature(right))


class ConvergenceTests(unittest.TestCase):
    def test_generalized_order_recovers_second_order(self) -> None:
        p = solve_observed_order(
            h_fine=0.25,
            h_medium=0.5,
            h_coarse=1.0,
            difference_fine_medium=0.1875,
            difference_medium_coarse=0.75,
        )
        self.assertIsNotNone(p)
        self.assertAlmostEqual(float(p), 2.0, places=8)

    def test_time_series_convergence_recovers_second_order(self) -> None:
        time = np.linspace(0.0, 1.0, 101)
        exact = 2.0 + np.sin(time)
        shape = 1.0 + 0.25 * np.cos(2.0 * time)
        fine = SeriesData(time, exact + 0.25**2 * shape)
        medium = SeriesData(time, exact + 0.5**2 * shape)
        coarse = SeriesData(time, exact + 1.0**2 * shape)
        result = convergence_estimate(
            fine=fine,
            medium=medium,
            coarse=coarse,
            h_fine=0.25,
            h_medium=0.5,
            h_coarse=1.0,
        )
        self.assertTrue(result.asymptotic_trend)
        self.assertIsNotNone(result.observed_order)
        self.assertAlmostEqual(float(result.observed_order), 2.0, places=7)
        self.assertIsNotNone(result.fine_grid_gci_l2)

    def test_exact_three_level_agreement_passes_without_fake_order(self) -> None:
        time = np.linspace(0.0, 1.0, 11)
        values = 2.0 + np.sin(time)
        result = convergence_estimate(
            fine=SeriesData(time, values),
            medium=SeriesData(time, values.copy()),
            coarse=SeriesData(time, values.copy()),
            h_fine=0.25,
            h_medium=0.5,
            h_coarse=1.0,
        )
        self.assertTrue(result.exact_agreement)
        self.assertTrue(result.asymptotic_trend)
        self.assertEqual(result.fine_grid_gci_l2, 0.0)
        self.assertIsNone(result.observed_order)

    def test_nonconvergent_sequence_has_no_positive_order(self) -> None:
        time = np.linspace(0.0, 1.0, 11)
        fine = SeriesData(time, np.ones_like(time))
        medium = SeriesData(time, np.ones_like(time) + 0.2)
        coarse = SeriesData(time, np.ones_like(time) + 0.25)
        result = convergence_estimate(
            fine=fine,
            medium=medium,
            coarse=coarse,
            h_fine=0.25,
            h_medium=0.5,
            h_coarse=1.0,
        )
        self.assertFalse(result.asymptotic_trend)
        self.assertIsNone(result.observed_order)


class EventTests(unittest.TestCase):
    def test_event_summary_and_tolerance(self) -> None:
        time = np.linspace(0.0, 1.0, 11)
        medium_values = (time >= 0.5).astype(float)
        fine_values = (time >= 0.4).astype(float)
        medium = event_summary(SeriesData(time, medium_values))
        fine = event_summary(SeriesData(time, fine_values))
        result = event_consistency(
            fine,
            medium,
            absolute_time_tolerance_s=0.02,
            period_s=0.2,
            period_fraction_tolerance=0.5,
            duty_fraction_tolerance=0.2,
            transition_count_tolerance=0,
        )
        self.assertTrue(result["pass"])
        self.assertAlmostEqual(result["allowed_first_on_time_difference_s"], 0.1)


class CampaignGenerationTests(unittest.TestCase):
    def test_generation_freezes_physical_signature(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base = root / "base.json"
            base.write_text(json.dumps(base_config()), encoding="utf-8")
            spec = {
                "schema_version": 1,
                "study_name": "unit test",
                "scope": "smoke",
                "base_config": "base.json",
                "case_overrides": {},
                "spatial_study": {
                    "target_bar_diameter_cells_worst_axis": [4.0, 6.0, 9.0],
                    "common_time_step_s": 7.5e-6,
                    "round_to_multiple": 2,
                },
                "temporal_study": {
                    "target_bar_diameter_cells_worst_axis": 9.0,
                    "time_step_s": [7.5e-6, 3.75e-6, 1.875e-6],
                },
                "observables": {
                    "smooth": ["funnel_depth_m"],
                    "binary": ["connection_candidate"],
                },
                "gates": {},
            }
            spec_path = root / "campaign.json"
            spec_path.write_text(json.dumps(spec), encoding="utf-8")
            output = root / "generated"
            manifest = generate_campaign(spec_path, output)
            self.assertEqual(len(manifest["cases"]), 6)
            self.assertEqual(manifest["unique_run_count"], 5)
            self.assertTrue(manifest["refinement_design_audit"]["pass"])
            aliases = [case for case in manifest["cases"] if "reuse_case_id" in case]
            self.assertEqual(len(aliases), 1)
            self.assertTrue((output / "cases_manifest.json").is_file())
            signatures = set()
            for case in manifest["cases"]:
                config = json.loads((output / case["config_file"]).read_text())
                signatures.add(json.dumps(physical_signature(config), sort_keys=True))
            self.assertEqual(len(signatures), 1)

    def test_design_gate_rejects_capillary_limited_registered_steps(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = base_config()
            config["high_fidelity"].update(
                radial_cells=136,
                azimuthal_cells=136,
                axial_cells=120,
            )
            cases = []
            for group, steps in {
                "spatial": [2.5e-5, 2.5e-5, 2.5e-5],
                "temporal": [1.0e-4, 5.0e-5, 2.5e-5],
            }.items():
                for level, step in enumerate(steps):
                    case = json.loads(json.dumps(config))
                    case["high_fidelity"]["time_step_s"] = step
                    name = f"{group}_{level}.json"
                    (root / name).write_text(json.dumps(case), encoding="utf-8")
                    cases.append(
                        {
                            "id": f"{group}_{level}",
                            "group": group,
                            "level": level,
                            "config_file": name,
                            "maximum_time_step_s": step,
                        }
                    )
            manifest = root / "cases_manifest.json"
            manifest.write_text(json.dumps({"cases": cases}), encoding="utf-8")
            result = audit_refinement_design(manifest)
            self.assertFalse(result["pass"])
            self.assertFalse(result["groups"]["spatial"]["pass"])
            self.assertFalse(result["groups"]["temporal"]["pass"])
            self.assertTrue(
                any("collapse" in reason for reason in result["reasons"])
            )
            report_root = root / "report"
            stdout = StringIO()
            with redirect_stdout(stdout):
                status = _analyse_command(
                    argparse.Namespace(
                        manifest=manifest,
                        runs=root / "runs",
                        output=report_root,
                    )
                )
            report = json.loads(
                (report_root / "qualification_report.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(status, 1)
            self.assertEqual(report["status"], "FAIL_REFINEMENT_DESIGN_GATE")


class CampaignRunnerTests(unittest.TestCase):
    def test_dry_run_selects_required_high_fidelity_backend(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = root / "cases_manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "cases": [
                            {
                                "id": "space_L0",
                                "config_file": "space_L0.json",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            arguments = argparse.Namespace(
                manifest=manifest,
                output=root / "runs",
                python="python3",
                resume=False,
                keep_going=False,
                dry_run=True,
            )
            stdout = StringIO()
            with patch(
                "hfq_cli_run.audit_refinement_design",
                return_value={"pass": True},
            ), redirect_stdout(stdout):
                status = _run_command(arguments)
            payload = json.loads(stdout.getvalue().splitlines()[-1])
            command = payload["commands"][0]
            self.assertEqual(status, 0)
            self.assertIn(
                ["--backend", "high_fidelity"],
                [command[index : index + 2] for index in range(len(command) - 1)],
            )


class EffectiveTimeStepGateTests(unittest.TestCase):
    @staticmethod
    def loaded_case(group: str, level: int, configured: float, actual: float) -> LoadedCase:
        return LoadedCase(
            case={"id": f"{group}_{level}", "group": group, "level": level},
            config={"high_fidelity": {"time_step_s": configured}},
            archived_config={"high_fidelity": {"time_step_s": configured}},
            time_s=np.asarray([0.0, 1.0]),
            observables={},
            fields={"diagnostic_dt_s": np.full(20, actual)},
            metadata={},
            manifest={},
            provenance={},
            result_status="completed",
        )

    def test_temporal_gate_rejects_collapsed_effective_steps(self) -> None:
        cases = [
            self.loaded_case("temporal", 0, 1.0e-4, 7.5e-6),
            self.loaded_case("temporal", 1, 5.0e-5, 7.5e-6),
            self.loaded_case("temporal", 2, 2.5e-5, 7.5e-6),
        ]
        result = _effective_time_step_gate("temporal", cases)
        self.assertFalse(result["pass"])
        self.assertFalse(result["effective_structure_valid"])

    def test_spatial_gate_accepts_a_common_exercised_step(self) -> None:
        cases = [
            self.loaded_case("spatial", level, 7.5e-6, 7.5e-6)
            for level in range(3)
        ]
        result = _effective_time_step_gate("spatial", cases)
        self.assertTrue(result["pass"])

    def test_archived_defaults_do_not_change_explicit_input_identity(self) -> None:
        generated = {
            "backend": "high_fidelity",
            "high_fidelity": {"time_step_s": 1.0e-5},
        }
        archived = {
            "backend": "high_fidelity",
            "high_fidelity": {
                "time_step_s": 1.0e-5,
                "pressure_solver_device": "auto",
            },
        }
        self.assertEqual(_explicit_config_differences(generated, archived), [])
        archived["high_fidelity"]["time_step_s"] = 2.0e-5
        self.assertEqual(
            _explicit_config_differences(generated, archived),
            ["high_fidelity.time_step_s"],
        )

    def test_terminal_failed_archive_is_not_misclassified_as_unreadable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / "fine_case"
            archive.mkdir()
            generated_config = {
                "backend": "high_fidelity",
                "high_fidelity": {"time_step_s": 1.0e-5},
            }
            archived_config = {
                "backend": "high_fidelity",
                "high_fidelity": {
                    "time_step_s": 1.0e-5,
                    "pressure_solver_device": "auto",
                },
            }
            source_digest = "a" * 64
            provenance = {
                "config_sha256": "formal-config-digest",
                "python": {"implementation": "CPython", "version": "3.12.3"},
                "dependencies": {"numpy": "2.5.2", "scipy": "1.17.1"},
                "resources": {
                    "peak_resident_set_size": {"value": 1024, "unit": "byte"}
                },
                "source_identity": {
                    "execution_source_identity_valid": True,
                    "source_tree_sha256_start": source_digest,
                    "source_tree_sha256_end": source_digest,
                },
            }
            payloads = {
                "config.json": json.dumps(archived_config, sort_keys=True),
                "provenance.json": json.dumps(provenance, sort_keys=True),
                "traceback.txt": "FloatingPointError: CFL exceeded\n",
            }
            file_identities = {}
            for name, content in payloads.items():
                encoded = content.encode("utf-8")
                (archive / name).write_bytes(encoded)
                file_identities[name] = {
                    "sha256": hashlib.sha256(encoded).hexdigest(),
                    "size_bytes": len(encoded),
                }
            (archive / "manifest.json").write_text(
                json.dumps(
                    {
                        "artifact_class": (
                            "terminal_diagnostic_without_simulation_result"
                        ),
                        "complete_run_archive": False,
                        "state": "failed",
                        "config_sha256": "formal-config-digest",
                        "duration_s": 12.5,
                        "progress_messages": 7,
                        "files": file_identities,
                        "execution_source_identity_valid": True,
                        "termination": {"completion_class": "failed"},
                        "error": {
                            "type": "FloatingPointError",
                            "message": "CFL exceeded",
                        },
                    }
                ),
                encoding="utf-8",
            )
            record = _terminal_failure_record(
                archive,
                generated_config=generated_config,
            )
            self.assertIsNotNone(record)
            assert record is not None
            self.assertEqual(record["state"], "failed")
            self.assertEqual(record["error"]["message"], "CFL exceeded")
            self.assertTrue(record["terminal_archive_payload_integrity_valid"])
            self.assertTrue(record["terminal_config_identity_valid"])
            self.assertTrue(record["generated_config_explicit_paths_match"])
            self.assertTrue(record["execution_source_identity_valid"])
            self.assertEqual(record["source_tree_sha256_start"], source_digest)
            self.assertEqual(
                record["execution_metrics"]["peak_resident_set_size_bytes"],
                1024,
            )

    def test_quality_gate_uses_dense_backend_maximum_over_sparse_frames(self) -> None:
        observables = {
            "liquid_mass_error_relative": np.asarray([0.0, 0.0]),
            "liquid_volume_balance_error_relative": np.asarray([0.0, 0.0]),
            "pressure_residual": np.asarray([5.0e-7, 5.0e-7]),
            "max_divergence": np.asarray([5.0e-5, 5.0e-5]),
            "cfl_advective": np.asarray([0.1, 0.1]),
        }
        loaded = LoadedCase(
            case={"id": "case"},
            config={"high_fidelity": {"time_step_s": 1.0e-5}},
            archived_config={"high_fidelity": {"time_step_s": 1.0e-5}},
            time_s=np.asarray([0.0, 1.0]),
            observables=observables,
            fields={},
            metadata={
                "diagnostics": {
                    "maximum_pressure_relative_residual": 9.0e-7,
                    "maximum_projected_divergence_s_inv": 2.0e-4,
                    "maximum_advective_cfl": 0.2,
                    "cumulative_phase_clip_relative": 0.0,
                    "cumulative_phase_repair_relative": 0.0,
                    "numerical_quality_gate": {"pass": True},
                }
            },
            manifest={},
            provenance={},
            result_status="completed",
        )
        result = _quality_gate(
            loaded,
            {
                "liquid_mass_error_relative_max": 1.0e-3,
                "liquid_volume_balance_error_relative_max": 1.0e-3,
                "pressure_residual_max": 1.0e-6,
                "projected_divergence_max_s_inv": 1.0e-4,
                "advective_cfl_max": 0.35,
                "cumulative_phase_clip_relative_max": 1.0e-3,
                "cumulative_phase_repair_relative_max": 1.0e-3,
            },
        )
        self.assertFalse(result["pass"])
        self.assertEqual(
            result["checks"]["projected_divergence_s_inv"]["value"],
            2.0e-4,
        )


if __name__ == "__main__":
    unittest.main()
