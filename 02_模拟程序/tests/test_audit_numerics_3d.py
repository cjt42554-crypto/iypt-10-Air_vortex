"""Adversarial regressions for the 2026-09-05 numerical audit fixes."""
from dataclasses import replace
import math
import unittest
from unittest.mock import patch

import numpy as np

from airvortex.core import HighFidelityConfig, SimulationConfig, RunStatus
from airvortex.numerics.cartesian3d import ImmersedCartesianGrid
from airvortex.numerics.interface3d import conservative_cls_3d, mixture_property_3d, topology_diagnostics_3d
from airvortex.numerics.phase_transfer3d import transport_momentum_with_phase_transfers
from airvortex.numerics.projection3d import VariableDensityProjection3D
from airvortex.hf3d import stepper
from airvortex.hf3d.types import HF3DState
from airvortex.backends.high_fidelity_3d import HighFidelityBackend
from test_high_fidelity_3d import small_hf3d_config


def small_grid():
    return ImmersedCartesianGrid(12, 12, 16, .045, .08,
        bar_half_length_m=.015, bar_radius_m=.003, bar_centre_z_m=.0045)


def kinetic(grid, density, velocity):
    return .5 * grid.integrate(density * sum(v*v for v in velocity))


class ConsistentCLSRegressionTests(unittest.TestCase):
    def test_uniform_velocity_for_density_ratios_and_multiple_transfers(self):
        g = small_grid()
        rng = np.random.default_rng(523)
        phase = np.where(g.active_mask, rng.uniform(0, 1, g.shape), 0.)
        for liquid, gas in ((10., 1.), (1000., 1.), (1e6, 1.), (1., 1000.)):
            with self.subTest(liquid=liquid, gas=gas):
                rho = mixture_property_3d(phase, liquid, gas)
                v = tuple(np.where(g.active_mask, a, 0.) for a in (1., -2., .25))
                initial_rho, initial_v = rho.copy(), v
                callbacks = 0
                def transport(transfers):
                    nonlocal rho, v, callbacks
                    for f, mask in zip(transfers, (g.open_x, g.open_y, g.open_z)):
                        self.assertTrue(np.all(f[~mask] == 0))
                    self.assertTrue(np.all(transfers[2][:, :, -1] == 0))
                    rho, v = transport_momentum_with_phase_transfers(rho, v, transfers, liquid-gas, g)
                    callbacks += 1
                final, diag = conservative_cls_3d(phase, g, 2.5*g.minimum_spacing_m,
                    pseudo_cfl=.2, iterations=12, transfer_callback=transport)
                self.assertEqual(callbacks, 12)
                self.assertGreater(np.max(np.abs(final-phase)), 1e-3)
                self.assertGreaterEqual(np.min(final), 0.)
                self.assertLessEqual(np.max(final), 1.)
                np.testing.assert_allclose(rho[g.active_mask], mixture_property_3d(final, liquid, gas)[g.active_mask], rtol=2e-12)
                for a, b in zip(v, initial_v):
                    np.testing.assert_allclose(a[g.active_mask], b[g.active_mask], atol=2e-12, rtol=2e-12)
                    self.assertAlmostEqual(g.integrate(rho*a), g.integrate(initial_rho*b), delta=2e-12*max(1., abs(g.integrate(initial_rho*b))))
                self.assertAlmostEqual(kinetic(g, rho, v), kinetic(g, initial_rho, initial_v), delta=2e-12*max(1.,kinetic(g, initial_rho, initial_v)))
                self.assertEqual(diag.clip_absolute_volume_m3, 0.)
                self.assertEqual(diag.repair_absolute_volume_m3, 0.)

    def test_bounded_mixing_conserves_momentum_without_creating_kinetic_energy(self):
        g = small_grid()
        rng = np.random.default_rng(59)
        phase = np.where(g.active_mask, rng.choice([0., .01, .99, 1.], g.shape), 0.)
        for liquid, gas in ((998.2, 1.204), (1.204, 998.2)):
            rho = mixture_property_3d(phase, liquid, gas)
            v = tuple(np.where(g.active_mask, rng.normal(size=g.shape), 0.) for _ in range(3))
            momentum = [g.integrate(rho*a) for a in v]
            energy = kinetic(g, rho, v)
            def transport(transfers):
                nonlocal rho, v
                before = kinetic(g, rho, v)
                rho, v = transport_momentum_with_phase_transfers(rho, v, transfers, liquid-gas, g)
                self.assertLessEqual(kinetic(g, rho, v), before + 1e-13)
            result, diag = conservative_cls_3d(phase, g, 8*g.minimum_spacing_m,
                pseudo_cfl=.2, iterations=25, transfer_callback=transport)
            self.assertTrue(np.all((result >= 0) & (result <= 1)))
            self.assertAlmostEqual(g.integrate(result), g.integrate(phase), delta=1e-17)
            np.testing.assert_allclose([g.integrate(rho*a) for a in v], momentum, rtol=1e-11, atol=1e-13)
            self.assertLess(kinetic(g, rho, v), energy)
            self.assertGreater(diag.limited_absolute_volume_m3, 0)

    def test_stepper_pairs_cls_with_momentum_before_viscous_or_body_forces(self):
        g = small_grid()
        config = small_hf3d_config()
        config = replace(config, high_fidelity=replace(config.high_fidelity, interface_compression_interval=1))
        phase = np.where(g.active_mask, .1+.8*(g.z_cell/g.height_m), 0.)
        old_rho = mixture_property_3d(phase, config.fluid.liquid_density_kg_m3, config.fluid.gas_density_kg_m3)
        state = HF3DState(0., 0, 0., np.zeros(g.face_shape_x), np.zeros(g.face_shape_y), np.zeros(g.face_shape_z), phase, np.zeros(g.shape))
        class ReachedViscous(Exception): pass
        def advect(*args, **kwargs):
            np.testing.assert_array_equal(args[7], old_rho)  # Must be BEFORE CLS.
            return *(np.where(g.active_mask, a, 0.) for a in (1., -.5, .25)), None
        def viscous(ux, uy, uz, viscosity, density, grid):
            for observed, expected in zip((ux,uy,uz), (1.,-.5,.25)):
                np.testing.assert_allclose(observed[g.active_mask], expected, rtol=1e-12, atol=1e-12)
            self.assertGreater(np.max(np.abs(density-old_rho)), 1e-3)
            raise ReachedViscous
        with patch.object(stepper, 'advect_momentum_3d', side_effect=advect), patch.object(stepper, 'newtonian_stress_acceleration_3d', side_effect=viscous):
            with self.assertRaises(ReachedViscous):
                stepper.advance_one_step(state, config, g, None, 1e-4, expected_liquid_volume_m3=g.integrate(phase))
        self.assertEqual(state.step, 0)
        np.testing.assert_array_equal(state.phase, phase)

    def test_material_advection_repair_is_not_silently_accepted(self):
        g = small_grid(); config = small_hf3d_config()
        phase = np.where(g.active_mask, .5, 0.)
        state = HF3DState(0.,0,0.,np.zeros(g.face_shape_x),np.zeros(g.face_shape_y),np.zeros(g.face_shape_z),phase,np.zeros(g.shape))
        original = stepper.advect_bounded_scalar
        def repaired(*args, **kwargs):
            c, diag = original(*args, **kwargs)
            return c, replace(diag, repair_absolute_integral=1e-8)
        with patch.object(stepper, 'advect_bounded_scalar', side_effect=repaired):
            with self.assertRaisesRegex(FloatingPointError, 'without matching momentum'):
                stepper.advance_one_step(state, config, g, None, 1e-4, expected_liquid_volume_m3=g.integrate(phase))
        self.assertEqual(state.step, 0)

    def test_real_backend_with_compression_on_records_new_diagnostics(self):
        for rpm in (0., 300., -300.):
            config = small_hf3d_config(rpm=rpm, duration_s=.02, cells=16)
            config = replace(config, high_fidelity=replace(config.high_fidelity,
                interface_compression_interval=1, pressure_solver_device='cpu'))
            result = HighFidelityBackend().run(config)
            self.assertEqual(result.status, RunStatus.COMPLETED)
            for key in ('diagnostic_cls_limited_volume_m3', 'diagnostic_pressure_effective_relative_tolerance', 'diagnostic_divergence_residual_identity_error_s_inv'):
                self.assertIn(key, result.fields)
                self.assertTrue(np.isfinite(result.fields[key]).all())


class ChannelClearanceRegressionTests(unittest.TestCase):
    def grid(self, ny=96):
        return ImmersedCartesianGrid(96, ny, 16, .048, .016,
            bar_half_length_m=.008, bar_radius_m=.0015, bar_centre_z_m=.0025)

    def classify(self, phase, g, width=.0025):
        return topology_diagnostics_3d(phase, g, interface_thickness_m=width, minimum_resolved_throat_cells=2)

    def test_single_voxel_slit_fails_even_when_equivalent_radius_passes(self):
        for ny, width in ((96,.0025), (48,.0015)):
            g=self.grid(ny); phase=np.ones(g.shape); phase[:,:,-2:]=0.
            phase[:,ny//2,:]=0.
            d=self.classify(phase,g,width)
            self.assertTrue(d.touches_bar)
            self.assertGreater(d.minimum_throat_radius_m,d.required_resolved_radius_m)
            self.assertFalse(d.resolved_channel)

    def test_remote_side_cavity_cannot_qualify_thin_path_to_bar(self):
        g=self.grid(); phase=np.ones(g.shape); phase[:,:,-2:]=0.
        phase[:,g.ny//2,:]=0.
        phase[((g.x_cell-.029)**2+g.y_cell**2)<.012**2]=0.
        d=self.classify(phase,g)
        self.assertTrue(d.touches_bar)
        self.assertGreater(d.minimum_throat_radius_m,d.required_resolved_radius_m)
        self.assertFalse(d.resolved_channel)

    def test_precontact_does_not_compute_distance_transform(self):
        g=self.grid(); phase=np.ones(g.shape); phase[:,:,-2:]=0.
        with patch('airvortex.numerics.interface3d.distance_transform_edt', side_effect=AssertionError('unneeded EDT')):
            self.assertFalse(self.classify(phase,g).resolved_channel)

    def test_wide_capped_bulk_channel_is_a_positive_control(self):
        g=self.grid(); phase=np.ones(g.shape)
        phase[(g.x_cell*g.x_cell+g.y_cell*g.y_cell<.013**2)&(g.z_cell>=g.bar_centre_z_m)]=0.
        d=self.classify(phase,g)
        self.assertTrue(d.resolved_channel)

    def test_thin_horizontal_link_between_displaced_columns_is_not_resolved(self):
        g=self.grid(); phase=np.ones(g.shape)
        phase[((g.x_cell+.021)**2+g.y_cell**2<.009**2)&(g.z_cell>=.008)]=0.
        phase[(g.x_cell**2+g.y_cell**2<.009**2)&(g.z_cell<.009)&(g.z_cell>=g.bar_centre_z_m)]=0.
        phase[(g.x_cell>-.03)&(g.x_cell<.009)&(abs(g.y_cell)<.009)&(g.z_cell>.008)&(g.z_cell<.009)]=0.
        d=self.classify(phase,g)
        self.assertTrue(d.touches_bar)
        self.assertGreater(d.minimum_throat_radius_m,d.required_resolved_radius_m)
        self.assertFalse(d.resolved_channel)


class ProjectionDivergenceTargetTests(unittest.TestCase):
    def test_target_controls_divergence_across_rhs_scales(self):
        g=small_grid(); rng=np.random.default_rng(104)
        density=mixture_property_3d(np.where(g.z_cell<.04,1.,0.),998.2,1.204)
        for scale,dt in ((.01,1e-4),(10.,.01)):
            v=tuple(rng.normal(scale=scale,size=shape)*mask for shape,mask in ((g.face_shape_x,g.open_x),(g.face_shape_y,g.open_y),(g.face_shape_z,g.open_z)))
            target=1e-7
            solver=VariableDensityProjection3D(g,relative_tolerance=1e-2,maximum_iterations=2000,maximum_divergence_s_inv=target)
            *_,d=solver.project(*v,density,dt)
            self.assertLess(d.effective_relative_tolerance,1e-2)
            self.assertLessEqual(d.divergence_after_s_inv,target)
            self.assertLess(d.divergence_residual_identity_error_s_inv,1e-9)
            self.assertLessEqual(abs(d.divergence_after_s_inv-d.residual_predicted_divergence_s_inv),d.divergence_residual_identity_error_s_inv+1e-14)

    def test_false_cg_success_is_rejected_by_actual_divergence(self):
        g=small_grid(); solver=VariableDensityProjection3D(g,relative_tolerance=.1,maximum_iterations=100,maximum_divergence_s_inv=1e-7)
        v=(np.ones(g.face_shape_x)*g.open_x,np.zeros(g.face_shape_y),np.zeros(g.face_shape_z))
        with patch('airvortex.numerics.projection3d.cg',return_value=(np.zeros(g.active_cell_count),0)):
            with self.assertRaisesRegex(FloatingPointError,'divergence target'):
                solver.project(*v,np.ones(g.shape),.001)
        self.assertIsNone(solver._last_pressure_solution)

    def test_configuration_roundtrip_and_invalid_targets(self):
        raw=SimulationConfig().to_dict()
        raw['high_fidelity']['maximum_projected_divergence_s_inv']=3e-6
        self.assertEqual(SimulationConfig.from_dict(raw).high_fidelity.maximum_projected_divergence_s_inv,3e-6)
        del raw['high_fidelity']['maximum_projected_divergence_s_inv']
        self.assertEqual(SimulationConfig.from_dict(raw).high_fidelity.maximum_projected_divergence_s_inv,1e-4)
        for bad in (0.,-1.,math.nan,math.inf,True):
            with self.assertRaises(ValueError):
                HighFidelityConfig(maximum_projected_divergence_s_inv=bad)


if __name__ == '__main__':
    unittest.main()
