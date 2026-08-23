from pathlib import Path
import tempfile
import unittest

import numpy as np

from airvortex.analysis import compare_results
from airvortex.io import load_run_archive, save_run_archive
from airvortex.stage1.backends import ForwardStage1Backend
from stage1_test_support import forward_smoke_config


class Stage1ArchiveRoundtripTests(unittest.TestCase):
    def test_archive_preserves_identity_camera_and_event_evidence(self) -> None:
        config = forward_smoke_config()
        result = ForwardStage1Backend().run(config)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "run"
            save_run_archive(path, config, result)
            restored = load_run_archive(path).result
        np.testing.assert_array_equal(restored.time_s, result.time_s)
        np.testing.assert_array_equal(
            restored.fields["event_evidence_time_s"],
            result.fields["event_evidence_time_s"],
        )
        np.testing.assert_array_equal(
            restored.fields["camera_boundary_height_m"],
            result.fields["camera_boundary_height_m"],
        )
        self.assertEqual(
            restored.metadata["observable_contract"],
            result.metadata["observable_contract"],
        )
        comparison = compare_results(
            restored, result, "visible_outer_to_center_depth_m"
        )
        self.assertEqual(comparison.metrics.rmse, 0.0)


if __name__ == "__main__":
    unittest.main()
