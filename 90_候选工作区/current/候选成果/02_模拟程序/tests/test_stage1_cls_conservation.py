import unittest

import numpy as np

from airvortex.numerics.cartesian3d import ImmersedCartesianGrid
from airvortex.stage1.cls3d import compress_phase_density_momentum_consistently


class Stage1CLSConservationTests(unittest.TestCase):
    def test_cls_reports_phase_density_and_three_momentum_components(self) -> None:
        grid = ImmersedCartesianGrid(
            8,
            8,
            10,
            0.04,
            0.08,
            bar_half_length_m=0.012,
            bar_radius_m=0.003,
            bar_centre_z_m=0.008,
        )
        phase = np.where(
            grid.active_mask,
            np.clip(0.5 + (0.04 - grid.z_cell) / 0.012, 0.0, 1.0),
            0.0,
        )
        density = np.where(grid.active_mask, 1.204 + (998.2 - 1.204) * phase, 0.0)
        ux = np.where(grid.active_mask, 0.01 * grid.x_cell / grid.radius_m, 0.0)
        uy = np.where(grid.active_mask, -0.01 * grid.y_cell / grid.radius_m, 0.0)
        uz = np.zeros(grid.shape)
        before_phase = grid.integrate(phase)
        before_mass = grid.integrate(density)
        updated, new_density, new_ux, new_uy, new_uz, diagnostics = (
            compress_phase_density_momentum_consistently(
                phase,
                density,
                ux,
                uy,
                uz,
                grid,
                2.0 * grid.minimum_spacing_m,
                pseudo_cfl=0.02,
                liquid_density_kg_m3=998.2,
                gas_density_kg_m3=1.204,
            )
        )
        self.assertLess(abs(grid.integrate(updated) - before_phase), 1.0e-15)
        self.assertLess(abs(grid.integrate(new_density) - before_mass), 1.0e-12)
        for key in (
            "phase_conservation_residual_m3",
            "density_conservation_residual_kg",
            "momentum_remap_x_kg_m_s",
            "momentum_remap_y_kg_m_s",
            "momentum_remap_z_kg_m_s",
            "momentum_remap_absolute_kg_m_s",
        ):
            self.assertIn(key, diagnostics)
            self.assertTrue(np.isfinite(diagnostics[key]))
        for value in (updated, new_density, new_ux, new_uy, new_uz):
            self.assertTrue(np.isfinite(value).all())


if __name__ == "__main__":
    unittest.main()
