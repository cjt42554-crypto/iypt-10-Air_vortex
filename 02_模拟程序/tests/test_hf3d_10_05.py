"""Targeted regression for the D3R1 pressure stopping-condition defect."""

from __future__ import annotations

import unittest

import numpy as np

from airvortex.numerics.cartesian3d import ImmersedCartesianGrid
from airvortex.numerics.interface3d import mixture_property_3d
from airvortex.numerics.projection3d import VariableDensityProjection3D


class D3R1PressureStoppingConditionRegression(unittest.TestCase):
    def test_relative_l2_success_does_not_imply_absolute_linf_divergence(self) -> None:
        """Reproduce the old contract gap and verify the current control.

        The legacy comparison uses the same current linear operator but disables
        the new absolute-divergence target.  This isolates the stopping rule from
        changes in geometry, density, forcing, or the pressure stencil.
        """

        grid = ImmersedCartesianGrid(
            12,
            12,
            16,
            0.045,
            0.080,
            bar_half_length_m=0.015,
            bar_radius_m=0.003,
            bar_centre_z_m=0.0045,
        )
        rng = np.random.default_rng(1005)
        velocity = tuple(
            rng.normal(size=shape) * open_mask
            for shape, open_mask in (
                (grid.face_shape_x, grid.open_x),
                (grid.face_shape_y, grid.open_y),
                (grid.face_shape_z, grid.open_z),
            )
        )
        density = mixture_property_3d(
            np.where(grid.z_cell < 0.050, 1.0, 0.0), 998.2, 1.204
        )
        configured_relative_tolerance = 1.0e-7
        maximum_divergence_s_inv = 1.0e-4

        legacy = VariableDensityProjection3D(
            grid,
            relative_tolerance=configured_relative_tolerance,
            maximum_iterations=2000,
        )
        *_, legacy_diagnostics = legacy.project(
            *velocity, density, 1.0e-4
        )
        self.assertLessEqual(
            legacy_diagnostics.pressure_relative_residual,
            configured_relative_tolerance,
        )
        self.assertGreater(
            legacy_diagnostics.divergence_after_s_inv,
            maximum_divergence_s_inv,
        )

        controlled = VariableDensityProjection3D(
            grid,
            relative_tolerance=configured_relative_tolerance,
            maximum_iterations=2000,
            maximum_divergence_s_inv=maximum_divergence_s_inv,
        )
        *_, controlled_diagnostics = controlled.project(
            *velocity, density, 1.0e-4
        )
        self.assertLess(
            controlled_diagnostics.effective_relative_tolerance,
            configured_relative_tolerance,
        )
        self.assertLessEqual(
            controlled_diagnostics.divergence_after_s_inv,
            maximum_divergence_s_inv,
        )
        self.assertLess(
            controlled_diagnostics.divergence_residual_identity_error_s_inv,
            1.0e-9,
        )
        self.assertLess(
            controlled_diagnostics.pressure_relative_residual,
            legacy_diagnostics.pressure_relative_residual,
        )


if __name__ == "__main__":
    unittest.main()
