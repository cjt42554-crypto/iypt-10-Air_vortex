import copy
import unittest

import numpy as np

from airvortex.analysis import (
    DataValidationError,
    compare_common_observables,
    compare_results,
    compare_series,
    series_from_result,
)
from airvortex.core import BackendMode, SimulationResult
from airvortex.stage1.contracts import contract_document


def result(
    backend: BackendMode,
    *,
    central_radius_m: float = 0.003,
    reference_source: str = "nominal_configured_liquid_depth_not_measured_H0",
) -> SimulationResult:
    parameters = {
        "central_observation_radius_m": central_radius_m,
        "outer_annulus_inner_radius_m": 0.036,
        "outer_annulus_outer_radius_m": 0.045,
        "interface_level": 0.5,
        "static_reference_height_m": 0.06,
        "static_reference_source": reference_source,
        "central_aggregation": "minimum",
        "outer_aggregation": "annular_area_weighted_mean",
    }
    return SimulationResult(
        backend,
        np.array([0.0, 1.0, 2.0]),
        {"surface_depth_m": np.array([0.0, 0.0, 1.0])},
        metadata={
            "backend_id": backend.value,
            "observable_units": {"surface_depth_m": "m"},
            "observable_contract": contract_document(
                ["surface_depth_m"],
                surface_operator_parameters=parameters,
            ),
        },
    )


class CoreComparisonContractTests(unittest.TestCase):
    def test_all_core_comparison_apis_reject_roi_mismatch(self) -> None:
        left = result(BackendMode.FORWARD)
        right = result(BackendMode.HIGH_FIDELITY, central_radius_m=0.006)
        with self.assertRaisesRegex(DataValidationError, "identity"):
            compare_results(left, right, "surface_depth_m")
        with self.assertRaisesRegex(DataValidationError, "identity"):
            compare_series(
                series_from_result(left, "surface_depth_m"),
                series_from_result(right, "surface_depth_m"),
                observable="surface_depth_m",
            )
        with self.assertRaisesRegex(DataValidationError, "no requested observable"):
            compare_common_observables(left, right, ["surface_depth_m"])

    def test_old_wrong_rmse_counterexample_is_rejected(self) -> None:
        left = result(BackendMode.FORWARD)
        raw = copy.deepcopy(dict(result(BackendMode.HIGH_FIDELITY).metadata))
        definition = raw["observable_contract"]["definitions"]["surface_depth_m"]
        definition["reference_surface"] = "different_reference_surface"
        right = SimulationResult(
            BackendMode.HIGH_FIDELITY,
            np.array([0.0, 1.0, 2.0]),
            {"surface_depth_m": np.array([0.0, 0.0, 1.0])},
            metadata=raw,
        )
        with self.assertRaisesRegex(DataValidationError, "stale or invalid"):
            compare_results(left, right, "surface_depth_m")

    def test_matching_complete_identity_is_comparable(self) -> None:
        comparison = compare_results(
            result(BackendMode.FORWARD),
            result(BackendMode.HIGH_FIDELITY),
            "surface_depth_m",
        )
        self.assertEqual(comparison.metrics.rmse, 0.0)


if __name__ == "__main__":
    unittest.main()
