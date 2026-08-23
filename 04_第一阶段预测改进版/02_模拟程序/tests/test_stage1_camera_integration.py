import unittest

import numpy as np

from airvortex.analysis import compare_series, series_from_experiment, series_from_result
from airvortex.analysis.experiment import (
    CALIBRATION,
    ColumnSpec,
    ExperimentDataset,
)
from airvortex.stage1.backends import ForwardStage1Backend
from airvortex.stage1.measurement import (
    CameraModel,
    project_phase_to_camera,
    project_surface_to_camera,
)
from airvortex.stage1.observables import SurfaceObservationOperator
from stage1_test_support import forward_smoke_config


class Stage1CameraIntegrationTests(unittest.TestCase):
    def test_camera_identity_covers_pixel_size_and_rejects_nonfinite_values(self) -> None:
        self.assertNotEqual(
            CameraModel(pixel_size_m=1.0e-4).operator_id,
            CameraModel(pixel_size_m=2.0e-4).operator_id,
        )
        for keyword in (
            {"pixel_size_m": float("nan")},
            {"psf_sigma_pixels": float("nan")},
            {"segmentation_threshold": float("nan")},
        ):
            with self.subTest(keyword=keyword):
                with self.assertRaises(ValueError):
                    CameraModel(**keyword)

    def test_projection_psf_pixelisation_and_segmentation_are_deterministic(self) -> None:
        phase = np.ones((8, 6, 10))
        phase[:, :, 7:] = 0.0
        phase[3:5, :, 4:8] = 0.0
        camera = CameraModel(pixel_size_m=1.0e-3, psf_sigma_pixels=0.5)
        first = project_phase_to_camera(
            phase,
            spacing_x_m=1.0e-3,
            spacing_y_m=1.0e-3,
            spacing_z_m=1.0e-3,
            camera=camera,
        )
        second = project_phase_to_camera(
            phase,
            spacing_x_m=1.0e-3,
            spacing_y_m=1.0e-3,
            spacing_z_m=1.0e-3,
            camera=camera,
        )
        np.testing.assert_array_equal(
            first["segmented_gas"], second["segmented_gas"]
        )
        self.assertEqual(
            first["metadata"]["measurement_operator_id"], camera.operator_id
        )
        self.assertIn("NOT_APPLIED", first["metadata"]["cylindrical_refraction"])

    def test_surface_camera_outputs_depth_and_channel_width(self) -> None:
        radius = np.linspace(0.00025, 0.04475, 90)
        profile = 0.06 - 0.055 * np.exp(-(radius / 0.004) ** 4)
        operator = SurfaceObservationOperator(
            0.003,
            0.036,
            0.045,
            0.5,
            0.06,
            "nominal_configured_liquid_depth_not_measured_H0",
        )
        measured = project_surface_to_camera(
            radius,
            profile,
            container_radius_m=0.045,
            container_height_m=0.08,
            bar_top_m=0.0075,
            camera=CameraModel(pixel_size_m=2.0e-4, psf_sigma_pixels=0.5),
            surface_operator=operator,
        )
        self.assertGreater(measured["camera_visible_depth_m"], 0.04)
        self.assertGreater(measured["camera_channel_width_m"], 0.0)

    def test_backend_archive_payload_and_experiment_comparison_share_camera_identity(self) -> None:
        result = ForwardStage1Backend().run(forward_smoke_config())
        self.assertEqual(
            result.fields["camera_boundary_height_m"].shape[0], result.time_s.size
        )
        camera_definition = result.metadata["observable_contract"]["definitions"][
            "camera_visible_depth_m"
        ]
        physical_definition = result.metadata["observable_contract"]["definitions"][
            "visible_outer_to_center_depth_m"
        ]
        self.assertNotEqual(
            camera_definition["identity_digest"],
            physical_definition["identity_digest"],
        )
        dataset = ExperimentDataset(
            time_s=result.time_s,
            observables={
                "camera_visible_depth_m": result.observables[
                    "camera_visible_depth_m"
                ]
            },
            role=CALIBRATION,
            columns=(
                ColumnSpec("time_s", "time", "s", "time"),
                ColumnSpec(
                    "camera_visible_depth_m",
                    "camera_visible_depth_m",
                    "m",
                    "observable",
                ),
            ),
            metadata={"observable_contract": result.metadata["observable_contract"]},
        )
        comparison = compare_series(
            series_from_result(result, "camera_visible_depth_m"),
            series_from_experiment(dataset, "camera_visible_depth_m"),
            observable="camera_visible_depth_m",
        )
        self.assertEqual(comparison.metrics.rmse, 0.0)


if __name__ == "__main__":
    unittest.main()
