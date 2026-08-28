from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parents[1]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from controlled_bar_geometry import (
    Capsule,
    GridSpec,
    adaptive_capsule_global_reference,
    adaptive_mask_capsule_intersection,
    build_g1_mask,
    capsule_signed_distance_m,
    circle_rectangle_area,
    exact_capsule_second_moment_tensor_m5,
    exact_capsule_volume_m3,
)


class D1GeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.grid6 = GridSpec(90, 90, 80, 0.045, 0.08)
        cls.nominal = Capsule(0.015, 0.003, (0.0, 0.0, 0.0045))
        cls.cache: dict[str, tuple[object, dict[str, object]]] = {}
        cls.nominal_build = build_g1_mask(
            cls.grid6, cls.nominal, selection_cache=cls.cache
        )
        phase = (0.49, 0.0, -0.49)
        cls.worst_phase_capsule = Capsule(
            0.015,
            0.003,
            (
                phase[0] * cls.grid6.dx,
                phase[1] * cls.grid6.dy,
                0.0045 + phase[2] * cls.grid6.dz,
            ),
        )
        cls.worst_phase_build = build_g1_mask(
            cls.grid6, cls.worst_phase_capsule, selection_cache=cls.cache
        )
        cls.grid9 = GridSpec(136, 136, 120, 0.045, 0.08)
        line_phase = (0.49, 0.49, 0.49)
        cls.line_phase_build = build_g1_mask(
            cls.grid9,
            Capsule(
                0.015,
                0.003,
                (
                    line_phase[0] * cls.grid9.dx,
                    line_phase[1] * cls.grid9.dy,
                    0.0045 + line_phase[2] * cls.grid9.dz,
                ),
            ),
            selection_cache=cls.cache,
        )

    def test_circle_rectangle_exact_special_cases(self) -> None:
        radius = 0.003
        full = circle_rectangle_area(radius, -radius, radius, -radius, radius)
        quarter = circle_rectangle_area(radius, 0.0, radius, 0.0, radius)
        self.assertAlmostEqual(full, math.pi * radius**2, places=19)
        self.assertAlmostEqual(quarter, 0.25 * math.pi * radius**2, places=19)
        self.assertEqual(
            circle_rectangle_area(radius, 2 * radius, 3 * radius, 0.0, radius),
            0.0,
        )

    def test_g1_nominal_hard_gates_and_symmetry(self) -> None:
        report = self.nominal_build.report
        self.assertTrue(report["hard_geometry_gate_pass"])
        self.assertEqual(report["solid_six_connected_component_count"], 1)
        mask = self.nominal_build.mask
        self.assertTrue(np.array_equal(mask, mask[::-1, :, :]))
        self.assertTrue(np.array_equal(mask, mask[:, ::-1, :]))

    def test_g1_respects_boundary_band_contract(self) -> None:
        grid = self.grid6
        capsule = self.nominal
        mask = self.nominal_build.mask
        x = grid.x_centres()
        y = grid.y_centres()
        z = grid.z_centres()
        half_diagonal = grid.cell_half_diagonal_m
        for i in range(grid.nx):
            for j in range(grid.ny):
                for k in range(grid.nz):
                    distance = capsule_signed_distance_m(x[i], y[j], z[k], capsule)
                    if distance <= -half_diagonal:
                        self.assertTrue(mask[i, j, k])
                    elif distance >= half_diagonal:
                        self.assertFalse(mask[i, j, k])

    def test_g1_worst_registered_phase_sample_passes(self) -> None:
        report = self.worst_phase_build.report
        errors = report["normalized_geometry_errors"]
        self.assertLessEqual(errors["bar_volume_relative"], 0.01)
        self.assertLessEqual(errors["centroid_over_bar_radius"], 0.05)
        self.assertLessEqual(errors["second_moment_relative_frobenius"], 0.05)
        self.assertEqual(report["solid_six_connected_component_count"], 1)

    def test_shifted_xy_phase_uses_exact_reachable_state_optimisation(self) -> None:
        staged = self.line_phase_build.report["optimisation"]["staged_selection"]
        self.assertEqual(
            staged["stage3_centroid"]["method"],
            "EXACT_ONE_LINE_X_POLYNOMIAL_REACHABLE_STATE_ENUMERATION",
        )
        self.assertEqual(
            staged["stage4_second_moment"]["method"],
            "EXACT_ONE_LINE_X_POLYNOMIAL_REACHABLE_STATE_ENUMERATION",
        )
        self.assertEqual(staged["stage3_centroid"]["objective_bracket_width"], 0.0)
        self.assertTrue(self.line_phase_build.report["hard_geometry_gate_pass"])

    def test_ulp_and_enumeration_invariance(self) -> None:
        centre = self.nominal.centre_xyz_m
        down = Capsule(
            self.nominal.half_length_m,
            self.nominal.radius_m,
            (centre[0], centre[1], float(np.nextafter(centre[2], -math.inf))),
        )
        up = Capsule(
            self.nominal.half_length_m,
            self.nominal.radius_m,
            (centre[0], centre[1], float(np.nextafter(centre[2], math.inf))),
        )
        variants = [
            build_g1_mask(self.grid6, down, selection_cache=self.cache),
            build_g1_mask(self.grid6, up, selection_cache=self.cache),
            build_g1_mask(
                self.grid6,
                self.nominal,
                enumeration_mode="reversed",
                selection_cache=self.cache,
            ),
            build_g1_mask(
                self.grid6,
                self.nominal,
                enumeration_mode="seeded",
                enumeration_seed=20260828,
                selection_cache=self.cache,
            ),
        ]
        expected_hash = self.nominal_build.report["mask_sha256"]
        expected_problem = self.nominal_build.report["selection_problem_sha256"]
        self.assertTrue(
            all(build.report["mask_sha256"] == expected_hash for build in variants)
        )
        self.assertTrue(
            all(
                build.report["selection_problem_sha256"] == expected_problem
                for build in variants
            )
        )

    def test_g2_global_intervals_meet_contract_and_contain_identities(self) -> None:
        reference = adaptive_capsule_global_reference(self.nominal)
        volume = exact_capsule_volume_m3(self.nominal)
        tensor = exact_capsule_second_moment_tensor_m5(self.nominal)
        self.assertLessEqual(reference["volume_relative_interval_width"], 1.0e-5)
        self.assertLessEqual(reference["centroid_over_radius_interval_width"], 1.0e-5)
        self.assertLessEqual(reference["second_moment_relative_interval_width"], 1.0e-4)
        self.assertLessEqual(reference["volume_interval_m3"][0], volume)
        self.assertGreaterEqual(reference["volume_interval_m3"][1], volume)
        self.assertLessEqual(
            reference["central_second_moment_interval_m5"][0][0][0], tensor[0, 0]
        )
        self.assertGreaterEqual(
            reference["central_second_moment_interval_m5"][0][0][1], tensor[0, 0]
        )

    def test_g2_mask_comparison_has_bounded_interval(self) -> None:
        comparison = adaptive_mask_capsule_intersection(
            self.grid6, self.nominal, self.nominal_build.selected_indices
        )
        interval = comparison["weighted_symmetric_difference_volume_interval_m3"]
        self.assertGreaterEqual(interval[0], 0.0)
        self.assertGreaterEqual(interval[1], interval[0])
        self.assertLessEqual(comparison["intersection_relative_interval_width"], 5.1e-5)


if __name__ == "__main__":
    unittest.main()
