import unittest

from airvortex.stage1.backends import ForwardStage1Backend
from stage1_test_support import forward_smoke_config


class Stage1UnitsTests(unittest.TestCase):
    def test_all_new_public_quantities_have_formal_units(self) -> None:
        result = ForwardStage1Backend().run(forward_smoke_config())
        units = result.metadata["observable_units"]
        for key in (
            "central_surface_min_height_m",
            "outer_reference_height_m",
            "central_depression_from_static_m",
            "visible_outer_to_center_depth_m",
            "surface_height_range_m",
            "camera_visible_depth_m",
            "camera_channel_width_m",
        ):
            self.assertIn(key, result.observables)
            self.assertEqual(units[key], "m")
        self.assertEqual(
            result.metadata["field_units"]["camera_segmented_gas_final"], "1"
        )


if __name__ == "__main__":
    unittest.main()
