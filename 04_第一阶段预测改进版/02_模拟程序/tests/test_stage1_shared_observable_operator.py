import unittest

import numpy as np

from airvortex.core import BackendMode, SimulationResult
from airvortex.stage1.contracts import contract_document
from airvortex.stage1.observables import (
    SurfaceObservationOperator,
    apply_surface_observation_operator,
    canonicalize_forward,
    canonicalize_hf3d,
)
from stage1_test_support import forward_smoke_config


class SharedObservableOperatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.radius = np.linspace(0.0005, 0.0445, 45)
        base = 0.06 - 0.004 * np.exp(-(self.radius / 0.004) ** 2)
        self.profiles = np.stack((base, base - 0.0005))
        self.operator = SurfaceObservationOperator(
            central_observation_radius_m=0.003,
            outer_annulus_inner_radius_m=0.036,
            outer_annulus_outer_radius_m=0.045,
            interface_level=0.5,
            static_reference_height_m=0.06,
            static_reference_source=(
                "nominal_configured_liquid_depth_not_measured_H0"
            ),
        )

    def _raw(self, backend: BackendMode) -> SimulationResult:
        return SimulationResult(
            backend,
            np.array([0.0, 1.0]),
            {"surface_profile_m": self.profiles},
            fields={"r_m": self.radius},
            metadata={
                "observable_units": {"surface_profile_m": "m"},
                "field_units": {"r_m": "m"},
            },
        )

    def test_synthetic_cross_representation_equivalence(self) -> None:
        config = forward_smoke_config()
        forward = canonicalize_forward(self._raw(BackendMode.FORWARD), config)
        hf3d = canonicalize_hf3d(
            self._raw(BackendMode.HIGH_FIDELITY), config
        )
        for key in (
            "central_surface_min_height_m",
            "outer_reference_height_m",
            "central_depression_from_static_m",
            "visible_outer_to_center_depth_m",
            "surface_height_range_m",
        ):
            np.testing.assert_allclose(
                forward.observables[key], hf3d.observables[key], atol=0.0
            )
            self.assertEqual(
                forward.metadata["observable_contract"]["definitions"][key][
                    "identity_digest"
                ],
                hf3d.metadata["observable_contract"]["definitions"][key][
                    "identity_digest"
                ],
            )
        np.testing.assert_allclose(
            forward.observables["visible_outer_to_center_depth_m"],
            forward.observables["outer_reference_height_m"]
            - forward.observables["central_surface_min_height_m"],
        )

    def test_roi_changes_output_and_scientific_identity(self) -> None:
        ring_profile = 0.06 - 0.004 * np.exp(
            -((self.radius - 0.006) / 0.001) ** 2
        )
        ring_profiles = np.stack((ring_profile, ring_profile - 0.0002))
        narrow = apply_surface_observation_operator(
            self.radius, ring_profiles, self.operator
        )
        wide_operator = SurfaceObservationOperator(
            central_observation_radius_m=0.009,
            outer_annulus_inner_radius_m=0.036,
            outer_annulus_outer_radius_m=0.045,
            interface_level=0.5,
            static_reference_height_m=0.06,
            static_reference_source=(
                "nominal_configured_liquid_depth_not_measured_H0"
            ),
        )
        wide = apply_surface_observation_operator(
            self.radius, ring_profiles, wide_operator
        )
        self.assertFalse(
            np.array_equal(
                narrow["central_surface_min_height_m"],
                wide["central_surface_min_height_m"],
            )
        )
        left = contract_document(
            ["surface_depth_m"],
            surface_operator_parameters=self.operator.parameters,
        )
        right = contract_document(
            ["surface_depth_m"],
            surface_operator_parameters=wide_operator.parameters,
        )
        self.assertNotEqual(
            left["definitions"]["surface_depth_m"]["identity_digest"],
            right["definitions"]["surface_depth_m"]["identity_digest"],
        )


if __name__ == "__main__":
    unittest.main()
