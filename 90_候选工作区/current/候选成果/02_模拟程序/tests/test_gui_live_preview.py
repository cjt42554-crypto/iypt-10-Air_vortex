"""Read-only solver-to-GUI live-surface preview gates."""

from __future__ import annotations

import unittest

from matplotlib.figure import Figure

from airvortex.core import BackendMode, SimulationConfig
from airvortex.gui.app import _surface_frame_from_progress
from airvortex.gui.plots import ProcessPlots


class LiveSurfacePreviewTests(unittest.TestCase):
    def test_progress_surface_is_strictly_validated(self) -> None:
        payload = {
            "simulated_time_s": 0.25,
            "metrics": {
                "surface_radius_m": [0.005, 0.015, 0.025],
                "surface_profile_m": [0.050, 0.055, 0.060],
                "surface_profile_minimum_m": [0.040, 0.050, 0.058],
                "surface_profile_maximum_m": [0.052, 0.057, 0.062],
            },
        }
        frame = _surface_frame_from_progress(payload)
        self.assertIsNotNone(frame)
        assert frame is not None
        self.assertEqual(frame.time_s, 0.25)
        self.assertEqual(frame.height_m[0], 0.050)
        self.assertEqual(frame.minimum_height_m[0], 0.040)
        self.assertEqual(frame.maximum_height_m[-1], 0.062)

        invalid = dict(payload)
        invalid["metrics"] = dict(payload["metrics"])
        invalid["metrics"]["surface_radius_m"] = [0.005, 0.004, 0.025]
        self.assertIsNone(_surface_frame_from_progress(invalid))

        invalid_envelope = dict(payload)
        invalid_envelope["metrics"] = dict(payload["metrics"])
        invalid_envelope["metrics"]["surface_profile_minimum_m"] = [0.060] * 3
        frame = _surface_frame_from_progress(invalid_envelope)
        self.assertIsNotNone(frame)
        assert frame is not None
        self.assertEqual(frame.minimum_height_m, ())
        self.assertEqual(frame.maximum_height_m, ())

    def test_apparatus_marks_live_profile_and_three_dimensional_envelope(self) -> None:
        frame = _surface_frame_from_progress(
            {
                "simulated_time_s": 0.0002,
                "metrics": {
                    "surface_radius_m": [0.005, 0.015, 0.025],
                    "surface_profile_m": [0.050, 0.055, 0.060],
                    "surface_profile_minimum_m": [0.040, 0.050, 0.058],
                    "surface_profile_maximum_m": [0.052, 0.057, 0.062],
                },
            }
        )
        assert frame is not None
        figure = Figure()
        axis = figure.add_subplot(1, 1, 1)
        config = SimulationConfig(backend=BackendMode.HIGH_FIDELITY)
        ProcessPlots._draw_apparatus(
            axis,
            None,
            config,
            0.0,
            live_surface_frame=frame,
        )
        self.assertIn("实时求解器自由表面", axis.get_title())
        self.assertIn("t = 0.0002 s", axis.get_title())
        _, labels = axis.get_legend_handles_labels()
        self.assertIn("三维方位最小–最大包络", labels)


if __name__ == "__main__":
    unittest.main()
