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

from hfq_failure_diagnostics import (  # noqa: E402
    ProjectionTrace,
    candidate_pressure_tolerance,
    combined_cfl_target_dt,
    diagnose_projection_trace,
    independent_cfl_limit_dt,
    load_projection_trace,
    retry_dt_from_post_cfl,
)


class CflControllerDesignTests(unittest.TestCase):
    def test_independent_limits_do_not_bound_the_split_sum(self) -> None:
        registered_dt = 7.5e-6
        hard_limit = 0.35
        rate = 0.349 / registered_dt
        source_rate_acceleration = 0.002 / registered_dt**2

        selected = independent_cfl_limit_dt(
            rate,
            source_rate_acceleration,
            hard_limit,
            registered_dt,
        )
        self.assertEqual(selected, registered_dt)
        split_cfl = rate * selected + source_rate_acceleration * selected**2
        self.assertGreater(split_cfl, hard_limit)

        target = 0.95 * hard_limit
        combined = combined_cfl_target_dt(
            rate,
            source_rate_acceleration,
            target,
            registered_dt,
        )
        self.assertLess(combined, registered_dt)
        self.assertLessEqual(
            rate * combined + source_rate_acceleration * combined**2,
            target * (1.0 + 1.0e-14),
        )

    def test_terminal_like_retry_targets_a_separate_margin(self) -> None:
        attempted = 7.5e-6
        proposed = retry_dt_from_post_cfl(
            attempted,
            observed_post_cfl=0.3504,
            hard_cfl_limit=0.35,
            target_fraction=0.95,
        )
        self.assertLess(proposed, attempted)
        self.assertAlmostEqual(
            0.3504 * proposed / attempted,
            0.95 * 0.35,
            places=14,
        )

    def test_passing_post_cfl_keeps_attempted_step(self) -> None:
        self.assertEqual(
            retry_dt_from_post_cfl(7.5e-6, 0.34, 0.35),
            7.5e-6,
        )


class PressureToleranceDesignTests(unittest.TestCase):
    def test_q1r_peak_recommends_stricter_decade_without_gate_change(self) -> None:
        self.assertEqual(
            candidate_pressure_tolerance(
                current_tolerance=1.0e-6,
                observed_max_divergence_s_inv=5.807218054645347e-4,
                divergence_gate_s_inv=1.0e-4,
            ),
            1.0e-7,
        )

    def test_passing_divergence_keeps_current_tolerance(self) -> None:
        self.assertEqual(
            candidate_pressure_tolerance(1.0e-6, 5.0e-5, 1.0e-4),
            1.0e-6,
        )


class ProjectionTraceTests(unittest.TestCase):
    def test_dense_trace_retains_peak_and_disjoint_gate_ranges(self) -> None:
        trace = ProjectionTrace(
            time_s=np.arange(6, dtype=float) * 0.1,
            dt_s=np.full(6, 0.1),
            divergence_s_inv=np.array([0.1, 0.3, 0.4, 0.1, 0.5, 0.2]),
            pressure_relative_residual=np.full(6, 9.0e-7),
            pressure_iterations=np.arange(6, dtype=float) + 10.0,
        )
        result = diagnose_projection_trace(trace, 0.25)
        self.assertFalse(result["gate_pass"])
        self.assertEqual(result["exceeding_step_count"], 3)
        self.assertEqual(
            result["exceeding_step_ranges_one_based_inclusive"],
            [[2, 3], [5, 5]],
        )
        self.assertEqual(result["peak"]["step_one_based"], 5)
        self.assertEqual(result["peak"]["pressure_iterations"], 14)

    def test_archive_name_mapping_is_resolved_before_loading_npz(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fields = {
                "diagnostic_step_time_s": "field_0000",
                "diagnostic_dt_s": "field_0001",
                "diagnostic_projected_divergence_s_inv": "field_0002",
                "diagnostic_pressure_relative_residual": "field_0003",
                "diagnostic_pressure_iterations": "field_0004",
            }
            result = {
                "arrays": {
                    "fields": {
                        name: {"npz_key": key} for name, key in fields.items()
                    }
                }
            }
            (root / "result.json").write_text(json.dumps(result), encoding="utf-8")
            np.savez(
                root / "arrays.npz",
                field_0000=np.array([0.1, 0.2]),
                field_0001=np.array([0.1, 0.1]),
                field_0002=np.array([1.0e-5, 2.0e-5]),
                field_0003=np.array([9.0e-7, 8.0e-7]),
                field_0004=np.array([12.0, 13.0]),
            )
            trace = load_projection_trace(root)
            np.testing.assert_allclose(trace.time_s, [0.1, 0.2])
            np.testing.assert_allclose(trace.divergence_s_inv, [1.0e-5, 2.0e-5])


if __name__ == "__main__":
    unittest.main()
