from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hfq_core import (  # noqa: E402
    SeriesData,
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
                    "common_time_step_s": 2.5e-5,
                    "round_to_multiple": 2,
                },
                "temporal_study": {
                    "target_bar_diameter_cells_worst_axis": 9.0,
                    "time_step_s": [1.0e-4, 5.0e-5, 2.5e-5],
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
            aliases = [case for case in manifest["cases"] if "reuse_case_id" in case]
            self.assertEqual(len(aliases), 1)
            self.assertTrue((output / "cases_manifest.json").is_file())
            signatures = set()
            for case in manifest["cases"]:
                config = json.loads((output / case["config_file"]).read_text())
                signatures.add(json.dumps(physical_signature(config), sort_keys=True))
            self.assertEqual(len(signatures), 1)


if __name__ == "__main__":
    unittest.main()
