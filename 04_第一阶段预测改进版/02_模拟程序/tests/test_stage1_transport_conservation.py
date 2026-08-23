import unittest

import numpy as np

from airvortex.numerics.cartesian3d import ImmersedCartesianGrid
from airvortex.stage1.transport3d import (
    advect_phase_density_momentum_consistently,
    donor_cell_outflow_rate_s_inv,
)


class Stage1TransportConservationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.grid = ImmersedCartesianGrid(
            8,
            8,
            10,
            0.04,
            0.08,
            bar_half_length_m=0.012,
            bar_radius_m=0.003,
            bar_centre_z_m=0.008,
        )

    def test_zero_flux_update_is_bounded_and_conservative(self) -> None:
        grid = self.grid
        phase = np.where(
            grid.active_mask,
            (grid.z_cell < 0.04).astype(float),
            0.0,
        )
        zero_cell = np.zeros(grid.shape)
        zero_x = np.zeros(grid.face_shape_x)
        zero_y = np.zeros(grid.face_shape_y)
        zero_z = np.zeros(grid.face_shape_z)
        updated, density, ux, uy, uz, diagnostics = (
            advect_phase_density_momentum_consistently(
                phase,
                zero_cell,
                zero_cell,
                zero_cell,
                zero_x,
                zero_y,
                zero_z,
                grid,
                1.0e-4,
                liquid_density_kg_m3=998.2,
                gas_density_kg_m3=1.204,
            )
        )
        np.testing.assert_allclose(updated, phase, atol=1.0e-14)
        self.assertTrue(np.all(density[grid.active_mask] > 0.0))
        self.assertLess(abs(diagnostics.phase_conservation_residual_m3), 1.0e-18)
        self.assertLess(abs(diagnostics.density_conservation_residual_kg), 1.0e-14)
        self.assertEqual(diagnostics.momentum_remap_absolute_kg_m_s, 0.0)
        for value in (ux, uy, uz):
            self.assertTrue(np.isfinite(value).all())

    def test_donor_cell_outflow_adds_opposing_faces(self) -> None:
        grid = self.grid
        ux = np.zeros(grid.face_shape_x)
        uy = np.zeros(grid.face_shape_y)
        uz = np.zeros(grid.face_shape_z)
        active = np.argwhere(grid.active_mask)[0]
        i, j, k = map(int, active)
        if 0 < i < grid.nx - 1:
            ux[i, j, k] = -2.0
            ux[i + 1, j, k] = 3.0
            rate = donor_cell_outflow_rate_s_inv(ux, uy, uz, grid)
            self.assertAlmostEqual(rate[i, j, k], 5.0 / grid.dx)


if __name__ == "__main__":
    unittest.main()
