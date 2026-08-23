import unittest

import numpy as np

from airvortex.core import BackendMode, Event, SimulationResult
from airvortex.stage1.backends import ForwardStage1Backend
from airvortex.stage1.contracts import PredictionState
from airvortex.stage1.observables import canonicalize_events
from stage1_test_support import forward_smoke_config


class Stage1EventsAndSamplingTests(unittest.TestCase):
    def test_dense_event_evidence_and_sparse_user_output_are_separate(self) -> None:
        result = ForwardStage1Backend().run(forward_smoke_config())
        np.testing.assert_allclose(result.time_s, [0.0, 0.005, 0.01])
        np.testing.assert_allclose(
            result.fields["event_evidence_time_s"],
            np.linspace(0.0, 0.01, 11),
        )
        self.assertEqual(result.fields["camera_boundary_height_m"].shape[0], 3)
        self.assertEqual(
            result.metadata["sampling_contract"]["event_evidence_samples"], 11
        )

    def test_all_real_event_spellings_map_to_prediction_states(self) -> None:
        expected = {
            "first_target_touch": PredictionState.GEOMETRIC_TOUCH,
            "gas_touches_bar": PredictionState.GEOMETRIC_TOUCH,
            "finite_channel": PredictionState.CONNECTED_UNRESOLVED,
            "resolved_channel": PredictionState.CONNECTED_RESOLVED,
            "channel_reconnect": PredictionState.CONNECTED_RESOLVED,
            "channel_reconnection": PredictionState.CONNECTED_RESOLVED,
            "channel_break": PredictionState.BROKEN_AFTER_CONNECTION,
            "sustained_channel": PredictionState.CONNECTED_SUSTAINED_NUMERICAL,
            "sustained_channel_numerical": (
                PredictionState.CONNECTED_SUSTAINED_NUMERICAL
            ),
            "domain_limit": PredictionState.DOMAIN_LIMIT,
            "model_domain_exit": PredictionState.INVALID_MODEL_DOMAIN,
            "numerical_failure": PredictionState.FAILED_NUMERICALLY,
        }
        events = tuple(
            Event(index * 0.001, kind)
            for index, kind in enumerate(expected, start=1)
        )
        result = SimulationResult(
            BackendMode.FORWARD,
            np.linspace(0.0, 0.02, 21),
            {"probe": np.zeros(21)},
            events=events,
            metadata={"observable_units": {"probe": "1"}},
        )
        adapted = canonicalize_events(result)
        states = {
            event.data["source_event_kind"]: event.data["prediction_state"]
            for event in adapted
        }
        self.assertEqual(
            states, {key: value.value for key, value in expected.items()}
        )
        self.assertTrue(
            all(event.data["bracket_width_s"] <= 0.001 + 1.0e-15 for event in adapted)
        )


if __name__ == "__main__":
    unittest.main()
