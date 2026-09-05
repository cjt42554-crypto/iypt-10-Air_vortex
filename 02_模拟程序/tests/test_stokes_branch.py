"""Independent small Stokes problems; never apparatus qualification."""
from dataclasses import replace
import json
import tempfile
from pathlib import Path
import unittest

import numpy as np

from airvortex.core import SimulationConfig
from airvortex.backends.forward_topology import ForwardTopologyBackend
from airvortex.io.archive import load_run_archive
from airvortex.runtime.operations import run_once
from airvortex.numerics.axisymmetric import AxisymmetricGrid
from airvortex.numerics.stokes_axisymmetric import AxisymmetricStokesOperator, axisymmetric_strain
from airvortex.numerics.stokes_spectrum import finite_depth_mobility, independent_finite_depth_mobility_bvp, PlanarStokesModeOperator
from validity_fixtures import ideal_test_reservoir


def sh_config(n=8, dt=0.001, amplitude=0.0):
    c = SimulationConfig()
    return replace(c,
        drive=replace(c.drive, initial_rpm=0., target_rpm=0., ramp_duration_s=0.),
        gas=ideal_test_reservoir(until_s=.02),
        forward=replace(c.forward, model_branch='AV-FWD-SH-v1'),
        run=replace(c.run, duration_s=.01, output_interval_s=.005),
        stokes=replace(c.stokes, radial_cells=n, axial_cells=n, time_step_s=dt,
                       initial_surface_mode=1 if amplitude else 0,
                       initial_surface_amplitude_m=amplitude))


def manufactured_errors(n):
    # psi=r²(1-r²)²z², u=-psi_z/r, w=psi_r/r, p=r²+z².
    # Body force = grad(p)-laplacian(vector u), mu=1 on R=H=1.
    g = AxisymmetricGrid(n,n,1.,1.)
    op = AxisymmetricStokesOperator(g,1.)
    r,z = g.r_faces[1:-1,None],g.z_centres[None,:]
    fr = 2*r-(-48*r**3*z+32*r*z)
    r,z = g.r_centres[:,None],g.z_faces[None,1:-1]
    fz = 2*z-(12*r**4+96*r**2*z*z-16*r*r-32*z*z+4)
    r = g.r_centres
    normal = 24*r**4-33*r*r+7
    r = g.r_faces[1:-1]
    shear = -2*r**5+28*r**3-18*r
    s = op.solve(fr,fz,normal,shear)
    r,z = g.r_faces[:,None],g.z_centres[None,:]
    u = -2*r*(1-r*r)**2*z
    r,z = g.r_centres[:,None],g.z_faces[None,:]
    w = 2*(1-r*r)*(1-3*r*r)*z*z
    r,z = g.r_centres[:,None],g.z_centres[None,:]
    p = r*r+z*z
    error = lambda a,b: float(np.linalg.norm(a-b)/(np.sqrt(a.size)+np.linalg.norm(b)))
    strain_ref = [(-2+12*r*r-10*r**4)*z, -2*(1-r*r)**2*z,
                  4*(1-r*r)*(1-3*r*r)*z,
                  (-2*r*(1-r*r)**2+(-16*r+24*r**3)*z*z)/2]
    errors = [error(s.radial_velocity_m_s,u), error(s.axial_velocity_m_s,w),error(s.pressure_pa,p)]
    errors += [error(a,b) for a,b in zip([s.strain.rr_s_inv,s.strain.tt_s_inv,s.strain.zz_s_inv,s.strain.rz_s_inv],strain_ref)]
    return errors, s.diagnostics


class StokesOperatorTests(unittest.TestCase):
    def test_manufactured_velocity_pressure_strain_convergence(self):
        grids=[16,32,64,128]
        rows=[]
        for n in grids:
            errors, diag=manufactured_errors(n)
            self.assertLess(diag.maximum_divergence_s_inv,2e-9)
            rows.append(errors)
        errors=np.array(rows)
        orders=np.log2(errors[:-1]/errors[1:])
        self.assertTrue(np.all(orders[-2:]>=1.5),orders)
        self.assertTrue(np.all(errors[-1]<1e-3),errors[-1])
        print('SH_MANUFACTURED',json.dumps({'n':grids,'errors_u_w_p_rr_tt_zz_rz':rows,'orders':orders.tolist()}))

    def test_pressure_gradient_and_uniform_traction_have_no_flow(self):
        g=AxisymmetricGrid(12,10,1.,1.); op=AxisymmetricStokesOperator(g,1.)
        r,z=g.r_centres[:,None],g.z_centres[None,:]
        p=r*r+z*z
        s=op.solve(np.diff(p,axis=0)/g.dr,np.diff(p,axis=1)/g.dz,
                   -(1.5*p[:,-1]-.5*p[:,-2]))
        self.assertLess(np.max(abs(s.radial_velocity_m_s)),1e-12)
        self.assertLess(np.max(abs(s.axial_velocity_m_s)),1e-12)
        np.testing.assert_allclose(s.pressure_pa,p,atol=1e-11)
        zero=op.solve(np.zeros((11,10)),np.zeros((12,9)),np.ones(12)*3.)
        self.assertLess(np.max(abs(zero.axial_velocity_m_s)),1e-12)
        np.testing.assert_allclose(zero.pressure_pa,-3.,atol=1e-11)

    def test_axisymmetric_strain_includes_hoop_component(self):
        g=AxisymmetricGrid(8,8,1.,1.)
        u=np.broadcast_to(2*g.r_faces[:,None],(9,8))
        w=np.broadcast_to(-4*g.z_faces[None,:],(8,9))
        d=axisymmetric_strain(g,u,w)
        np.testing.assert_allclose(d.rr_s_inv,2.,atol=1e-14)
        np.testing.assert_allclose(d.tt_s_inv,2.,atol=1e-14)
        np.testing.assert_allclose(d.zz_s_inv,-4.,atol=1e-14)
        np.testing.assert_allclose(d.rz_s_inv,0.,atol=1e-14)

    def test_planar_mobility_three_independent_routes_and_limits(self):
        rows=[]
        for k,ref in [(1.,.3274408882489201),(10.,12.029084059132849)]:
            self.assertAlmostEqual(finite_depth_mobility(k,.1,.001)/ref,1.,delta=1e-10)
            bvp=independent_finite_depth_mobility_bvp(k,.1,.001)
            self.assertAlmostEqual(bvp/ref,1.,delta=1e-9)
            errors=[]
            for n in [12,24,48]:
                value,_=PlanarStokesModeOperator(n).mobility(k,.1,.001)
                errors.append(abs(value-ref)/ref)
            self.assertLess(errors[-1],1e-3)
            self.assertGreater(np.log2(errors[-2]/errors[-1]),1.5)
            rows.append({'k':k,'reference':ref,'bvp':bvp,'fd_errors':errors})
        self.assertEqual(finite_depth_mobility(0.,.1,.001),0.)
        self.assertAlmostEqual(finite_depth_mobility(1e-6,.1,.001)/(1e-12*.1**3/(3*.001)),1.,delta=1e-12)
        self.assertAlmostEqual(finite_depth_mobility(1e5,.1,.001)*2*.001*1e5,1.)
        print('SH_PLANAR',json.dumps(rows))


class StokesBackendTests(unittest.TestCase):
    def test_stationary_hydrostatic_archive_and_explicit_branch(self):
        c=sh_config()
        with tempfile.TemporaryDirectory() as d:
            outcome=run_once(c,Path(d)/'sh')
            r=load_run_archive(Path(d)/'sh').result
            np.testing.assert_array_equal(r.observables['surface_profile_m'],c.container.liquid_depth_m)
            np.testing.assert_array_equal(r.observables['quantitative_valid_mask'],1.)
            np.testing.assert_array_equal(r.fields['axial_velocity_m_s'],0.)
            self.assertFalse(r.metadata['uses_cp_interface_mobility'])
            self.assertFalse(r.metadata['nonlinear_moving_domain_implemented'])
            from airvortex.gui.model import normalize_simulation_result
            view=normalize_simulation_result(r,c.backend)
            self.assertEqual(len(view.surface_frames),3)
            self.assertEqual(len(view.field_frames),3)
            self.assertIn('移动面能量', ' '.join(view.warnings))

    def test_missing_gas_and_large_surface_exit_strictly(self):
        c=sh_config()
        r=ForwardTopologyBackend().run(replace(c,gas=SimulationConfig().gas))
        self.assertEqual(r.time_s.size,1)
        self.assertEqual(r.metadata['quantitative_validity']['reason'],'GAS_PRESSURE_EVIDENCE_MISSING')
        c=sh_config(amplitude=.002)
        c=replace(c,stokes=replace(c.stokes,maximum_surface_slope=.001))
        r=ForwardTopologyBackend().run(c)
        np.testing.assert_array_equal(r.observables['quantitative_valid_mask'],0.)
        self.assertEqual(r.time_s.size,1)

    def test_unsupported_nonlinear_domain_is_not_aliased(self):
        c=sh_config()
        with self.assertRaisesRegex(ValueError,'not implemented'):
            replace(c.stokes,domain_model='nonlinear_moving_domain')

    def test_linear_solver_failure_is_not_a_physical_exit(self):
        from unittest.mock import patch
        original=AxisymmetricStokesOperator.solve
        def inaccurate(op,*args,**kwargs):
            value=original(op,*args,**kwargs)
            return replace(value,diagnostics=replace(value.diagnostics,relative_linear_residual=1.))
        with patch.object(AxisymmetricStokesOperator,'solve',inaccurate):
            with tempfile.TemporaryDirectory() as d:
                outcome=run_once(sh_config(),Path(d)/'failed')
                self.assertFalse(outcome.succeeded)
                manifest=json.loads((outcome.output/'manifest.json').read_text())
                self.assertEqual(manifest['state'],'failed')
                self.assertEqual(manifest['quantitative_validity']['status'],'numerical_failure')

if __name__=='__main__':
    unittest.main()
