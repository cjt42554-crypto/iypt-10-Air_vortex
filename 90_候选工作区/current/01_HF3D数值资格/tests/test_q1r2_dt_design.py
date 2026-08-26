from __future__ import annotations

import unittest

import numpy as np

from analyse_q1r2_dt_pilot import select_common_dt


class Q1R2TimeStepDesignTests(unittest.TestCase):
    def test_selects_largest_quantised_step_with_buffer(self) -> None:
        report = select_common_dt(
            {
                "coarse": np.full(100, 7.5e-6),
                "medium": np.full(100, 7.5e-6),
                "fine": np.concatenate(
                    [np.full(96, 7.5e-6), np.full(4, 6.0e-6)]
                ),
            },
            pilot_maximum_s=7.5e-6,
            acceptance_fraction=0.95,
            design_fraction=0.97,
            quantum_s=1.0e-7,
        )
        self.assertAlmostEqual(report["selected"]["candidate_s"], 6.0e-6)
        self.assertAlmostEqual(
            report["immediately_larger_rejected_candidate"]["candidate_s"],
            6.1e-6,
        )

    def test_requires_design_fraction_above_acceptance(self) -> None:
        with self.assertRaisesRegex(ValueError, "must exceed"):
            select_common_dt(
                {"fine": np.full(10, 1.0e-6)},
                pilot_maximum_s=1.0e-6,
                acceptance_fraction=0.95,
                design_fraction=0.95,
                quantum_s=1.0e-7,
            )

    def test_rejects_nonfinite_trajectory(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid pilot step series"):
            select_common_dt(
                {"fine": np.array([1.0e-6, np.nan])},
                pilot_maximum_s=1.0e-6,
                acceptance_fraction=0.95,
                design_fraction=0.97,
                quantum_s=1.0e-7,
            )


if __name__ == "__main__":
    unittest.main()
