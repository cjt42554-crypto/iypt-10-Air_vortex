"""Local-film constitutive, nonlinear and formal-entry verification."""
from dataclasses import replace
import json
import math
from pathlib import Path
import tempfile
import unittest

import numpy as np
from scipy.integrate import quad
from scipy.special import j0, jn_zeros

from airvortex.core import SimulationConfig
from airvortex.backends.forward_topology import ForwardTopologyBackend
from airvortex.io.archive import load_run_archive
from airvortex.runtime.operations import run_once
from airvortex.numerics.thin_film import (
    AxisymmetricThinFilm,ThinFilmBoundary,NonpositiveFilmError,
    film_mobility_m3_pa_s,wetting_energy_j_m2,wetting_pressure_pa,
    wetting_pressure_derivative_pa_m,weighted_swirl_flux_m2_s,
    solve_uniform_drainage,integrate_positive_thin_film,
)


def operator(n=32, boundary=None, wetting=1., closure='none', omega=None):
    return AxisymmetricThinFilm(radius_m=.003,radial_cells=n,density_kg_m3=1000.,
        viscosity_pa_s=.001,surface_tension_n_m=.072,density_difference_kg_m3=998.8,
        gravity_m_s2=9.81,slip_length_m=1e-6,precursor_thickness_m=1e-5,
        wetting_pressure_scale_pa=wetting,boundary=boundary or ThinFilmBoundary.closed(),
        swirl_closure=closure,angular_speed=omega or (lambda t:(0.,0.)))


def integrate(op,h,times,max_dt=1e-3,rtol=1e-10,minimum=1e-9,diagnostic=False):
    return integrate_positive_thin_film(op,h,times,minimum_valid_thickness_m=minimum,
        maximum_time_step_s=max_dt,minimum_time_step_s=1e-12,
        relative_tolerance=rtol,absolute_tolerance_m=1e-15,
        energy_absolute_tolerance_j=1e-20,volume_absolute_tolerance_m3=1e-23,
        maximum_step_rejections=8,diagnostic_continuation=diagnostic)


class ThinFilmTests(unittest.TestCase):
    def test_mobility_and_wetting_have_independent_references(self):
        h,mu,b=1e-4,.001,2e-6
        # Unit negative pressure gradient: u=(hy-y²/2+bh)/mu.
        q=quad(lambda y:(h*y-y*y/2+b*h)/mu,0,h,epsabs=1e-22,epsrel=1e-12)[0]
        self.assertAlmostEqual(float(film_mobility_m3_pa_s(h,mu,b))/q,1.,delta=1e-12)
        delta=1e-9
        derivative=(wetting_energy_j_m2(h+delta,1.,1e-5)-wetting_energy_j_m2(h-delta,1.,1e-5))/(2*delta)
        self.assertAlmostEqual(float(derivative/wetting_pressure_pa(h,1.,1e-5)),1.,delta=1e-8)
        curvature=(wetting_pressure_pa(h+delta,1.,1e-5)-wetting_pressure_pa(h-delta,1.,1e-5))/(2*delta)
        self.assertAlmostEqual(float(curvature/wetting_pressure_derivative_pa_m(h,1.,1e-5)),1.,delta=1e-8)

    def test_equal_mean_swirl_has_different_weighted_transport(self):
        profiles=[(1.,),(0.,3.,-1.5)] # Both have mean 1; f'(1)=0.
        values=[]
        for coefficients in profiles:
            value=float(weighted_swirl_flux_m2_s(1e-4,.002,10.,1000.,.001,1e-6,coefficients))
            f=lambda x:sum(c*x**i for i,c in enumerate(coefficients))
            ref=1000.*100.*.002/.001*quad(lambda x:(1e-6*1e-8+1e-12*(x-x*x/2))*f(x)**2,0,1,epsabs=1e-24)[0]
            self.assertAlmostEqual(value/ref,1.,delta=1e-12)
            values.append(value)
        self.assertGreater(abs(values[1]/values[0]-1.),.1)
        self.assertEqual(float(weighted_swirl_flux_m2_s(1e-4,0.,10.,1000.,.001,0.,(1.,))),0.)

    def test_uniform_drainage_constant_and_changing_speed(self):
        for omega,integral in [(lambda t:10.,10.),(lambda t:10.+100.*t,70./3.)]:
            measured=solve_uniform_drainage(1e-4,[0.,.1],density_kg_m3=1000.,
                viscosity_pa_s=.001,slip_length_m=0.,angular_speed_rad_s=omega)[-1]
            ref=(1e8+4*1000./(.003)*integral)**(-.5)
            self.assertAlmostEqual(measured/ref,1.,delta=1e-9)

    def test_nonlinear_semidiscrete_energy_open_and_closed(self):
        for boundary in [ThinFilmBoundary.closed(),ThinFilmBoundary.open_constant(1e-4,1.2)]:
            for closure in ['none','corotation','prescribed_depth_profile']:
                op=operator(boundary=boundary,closure=closure,omega=lambda t:(2.+t,1.))
                h=1e-4+1e-5*np.cos(np.pi*op.radius_centres_m/op.radius_m)
                value=op.evaluate(h,.02)
                eps=1e-7
                derivative=(op.energy_j(h+eps*value.thickness_rate_m_s,.02+eps)
                            -op.energy_j(h-eps*value.thickness_rate_m_s,.02-eps))/(2*eps)
                rhs=-value.dissipation_power_w+value.drive_power_w+value.boundary_power_w
                self.assertLess(abs(derivative-rhs)/(abs(rhs)+1e-12),1e-6)
                volume_rate=2*np.pi*np.dot(op.annular_weights_m2,value.thickness_rate_m_s)
                self.assertLess(abs(volume_rate+value.boundary_outflow_m3_s),1e-20)
        with self.assertRaises(NonpositiveFilmError):
            op.evaluate(np.zeros(op.radial_cells),0.)

    def test_nonlinear_evolution_mass_energy_and_spatial_convergence(self):
        rows=[]; profiles=[]; grids=[16,32,64,128,256]
        for n in grids:
            op=operator(n)
            h=1e-4+2e-5*j0(jn_zeros(1,1)[0]*op.radius_centres_m/op.radius_m)
            traj=integrate(op,h,[0.,.05])
            self.assertEqual(traj.status,'completed')
            last=traj.thickness_m[-1]; profiles.append(last)
            v0=op.volume_m3(h); e0=op.energy_j(h,0.)
            dv=(op.volume_m3(last)-v0+traj.cumulative_boundary_outflow_m3[-1])/v0
            energy_error=(op.energy_j(last,.05)-e0+traj.cumulative_dissipation_j[-1])/abs(e0)
            self.assertLess(abs(dv),1e-6); self.assertLess(abs(energy_error),1e-6)
            self.assertGreater(np.min(last),op.precursor_thickness_m)
            self.assertLess(op.energy_j(last,.05),e0)
            rows.append({'n':n,'volume_relative_error':dv,'energy_relative_error':energy_error})
        # Compare the same physical cell-centred solution by cubic interpolation;
        # reference 256 and independent extra 128 refinement are both retained.
        from scipy.interpolate import CubicSpline
        ref_radius=operator(256).radius_centres_m
        ref=CubicSpline(ref_radius,profiles[-1])
        errors=[]
        for n,h in zip(grids[:-1],profiles[:-1]):
            target=ref(operator(n).radius_centres_m)
            errors.append(float(np.linalg.norm(h-target)/(np.sqrt(n)*1e-4+np.linalg.norm(target))))
        self.assertLess(errors[2],1e-3)
        self.assertGreater(np.log2(errors[0]/errors[1]),1.5)
        self.assertGreater(np.log2(errors[1]/errors[2]),1.5)
        self.assertLess(errors[-1],errors[1]/10.)
        print('TF_NONLINEAR_SPACE',json.dumps({'ledger':rows,'errors_vs_256':errors}))

    def test_stable_and_unstable_linear_disk_modes(self):
        for wetting, expected_sign in [(1.,-1),(200.,1)]:
            op=operator(128,wetting=wetting)
            h0=2e-5; k=jn_zeros(1,1)[0]/op.radius_m
            mode=j0(k*op.radius_centres_m)
            amp=1e-10
            rate=op.evaluate(h0+amp*mode,0.).thickness_rate_m_s
            measured=np.dot(op.annular_weights_m2*mode,rate)/(amp*np.dot(op.annular_weights_m2,mode*mode))
            expected=-float(film_mobility_m3_pa_s(h0,.001,1e-6))*k*k*(.072*k*k+998.8*9.81+float(wetting_pressure_derivative_pa_m(h0,wetting,1e-5)))
            self.assertEqual(np.sign(expected),expected_sign)
            self.assertAlmostEqual(measured/expected,1.,delta=1e-3)

    def test_nonlinear_time_refinement_with_independent_radau_reference(self):
        from scipy.integrate import solve_ivp
        op=operator(16)
        h=1e-4+2e-5*j0(jn_zeros(1,1)[0]*op.radius_centres_m/op.radius_m)
        references=[]
        for tol in [1e-10,1e-12]:
            ref=solve_ivp(lambda t,y:op.evaluate(y,t).thickness_rate_m_s,
                (0.,.2),h,method='Radau',rtol=tol,atol=1e-16,max_step=.0005)
            self.assertTrue(ref.success)
            references.append(ref.y[:,-1])
        normalizer=np.sqrt(h.size)*1e-4+np.linalg.norm(references[-1])
        reference_error=np.linalg.norm(references[-2]-references[-1])/normalizer
        maximum_steps=[.02,.01,.005,.0025]
        errors=[]
        for dt in maximum_steps:
            traj=integrate(op,h,[0.,.2],max_dt=dt,rtol=1e-3)
            self.assertEqual(traj.status,'completed')
            errors.append(float(np.linalg.norm(traj.thickness_m[-1]-references[-1])/normalizer))
        tolerances=[1e-3,1e-5,1e-7,1e-9]
        tolerance_errors=[]
        for tolerance in tolerances:
            traj=integrate(op,h,[0.,.2],max_dt=.02,rtol=tolerance)
            self.assertEqual(traj.status,'completed')
            tolerance_errors.append(float(np.linalg.norm(traj.thickness_m[-1]-references[-1])/normalizer))
        print('TF_NONLINEAR_TIME',json.dumps({'maximum_steps_s':maximum_steps,
            'step_sequence_rtol':1e-3,'errors_vs_radau':errors,
            'relative_tolerances':tolerances,'tolerance_sequence_max_step_s':.02,
            'tolerance_errors_vs_radau':tolerance_errors,
            'radau_refinement_error':float(reference_error)}))
        # The production BDF is adaptive in both order (1--5) and step size:
        # max_dt only caps its steps. Tiny changes can select another order
        # history, so successive cap halvings need not reduce error strictly.
        # Retain the 1e-6 accuracy target at every level and require substantial
        # coarse-to-fine improvement in two independent controls. This does not
        # assign a fixed convergence order to adaptive BDF.
        self.assertLess(max(errors+tolerance_errors),1e-6)
        self.assertLess(errors[-1],errors[0]/10.)
        self.assertLess(tolerance_errors[-1],tolerance_errors[0]/100.)
        self.assertLess(reference_error,min(errors+tolerance_errors)/10.)

    def test_open_pde_drainage_and_thickness_exit(self):
        exact=lambda t:(1e8+4*1000./(.003)*100.*t)**(-.5)
        rate=lambda t:-2.*1000.*100./(.003)*exact(t)**3
        boundary=ThinFilmBoundary('open',exact,rate,lambda t:998.8*9.81*exact(t))
        errors=[]
        for n in [24,48,96,192]:
            op=operator(n,boundary=boundary,wetting=0.,closure='corotation',omega=lambda t:(10.,0.))
            op.slip_length_m=0. # fixed physical no-slip input on every grid
            h=np.full(n,1e-4)
            traj=integrate(op,h,[0.,.05,.1])
            errors.append(float(np.max(abs(traj.thickness_m[-1]-exact(.1)))/exact(.1)))
        self.assertTrue(np.all(np.log2(np.array(errors[:-1])/errors[1:])>1.5))
        print('TF_OPEN_DRAINAGE',json.dumps({'n':[24,48,96,192],'relative_errors':errors}))
        self.assertEqual(traj.status,'completed')
        self.assertLess(np.max(abs(traj.thickness_m[-1]-exact(.1)))/exact(.1),1e-6)
        self.assertLess(abs(op.volume_m3(traj.thickness_m[-1])-op.volume_m3(h)+traj.cumulative_boundary_outflow_m3[-1])/op.volume_m3(h),1e-6)
        e0=op.energy_j(h,0.)
        residual=op.energy_j(traj.thickness_m[-1],.1)-e0+traj.cumulative_dissipation_j[-1]-traj.cumulative_drive_work_j[-1]-traj.cumulative_boundary_work_j[-1]
        self.assertLess(abs(residual)/(abs(e0)+abs(traj.cumulative_dissipation_j[-1])),1e-6)
        exit_time=(1/(.98e-4)**2-1e8)/(4*1000./(.003)*100.)
        stopped=integrate(op,h,[0.,.05,.1],minimum=.98e-4)
        self.assertEqual(stopped.status,'physical_exit')
        lo,hi=stopped.termination_interval_s
        self.assertLessEqual(lo,exit_time);self.assertGreaterEqual(hi,exit_time)
        self.assertGreater(np.min(stopped.thickness_m),.98e-4)

    def test_formal_local_film_archive_and_unambiguous_scope(self):
        c=SimulationConfig()
        c=replace(c,forward=replace(c.forward,model_branch='AV-FWD-TF-v1'),
            drive=replace(c.drive,initial_rpm=0.,target_rpm=0.,ramp_duration_s=0.),
            run=replace(c.run,duration_s=.01,output_interval_s=.005),
            film=replace(c.film,radial_cells=16,swirl_closure='none',initial_perturbation_amplitude_m=1e-5))
        with tempfile.TemporaryDirectory() as d:
            outcome=run_once(c,Path(d)/'film')
            r=load_run_archive(outcome.output).result
            self.assertEqual(r.metadata['model_id'],'AV-FWD-TF-v1')
            self.assertIn('not a whole-vessel',r.metadata['scope'])
            self.assertFalse(r.metadata['quantitative_validity']['apparatus_quantitative_prediction_qualified'])
            self.assertEqual(r.fields['film_thickness_m'].shape,(3,16))
            np.testing.assert_array_equal(r.observables['quantitative_valid_mask'],1.)
            from airvortex.gui.model import normalize_simulation_result
            view=normalize_simulation_result(r,c.backend)
            self.assertEqual(len(view.surface_frames),3)
            np.testing.assert_allclose(view.surface_frames[-1].height_m,r.fields['film_thickness_m'][-1])

    def test_diagnostic_tail_uses_same_monotone_mask_contract(self):
        c=SimulationConfig()
        c=replace(c,forward=replace(c.forward,model_branch='AV-FWD-TF-v1',prediction_mode='diagnostic'),
            drive=replace(c.drive,initial_rpm=10.*60/(2*np.pi),target_rpm=10.*60/(2*np.pi),ramp_duration_s=0.),
            run=replace(c.run,duration_s=.1,output_interval_s=.02),
            film=replace(c.film,radial_cells=16,boundary_condition='open',wetting_pressure_pa=0.,
                minimum_valid_thickness_m=.99e-4,outer_pressure_pa=(c.fluid.liquid_density_kg_m3-c.fluid.gas_density_kg_m3)*c.fluid.gravity_m_s2*1e-4))
        r=ForwardTopologyBackend().run(c)
        self.assertEqual(r.metadata['quantitative_validity']['status'],'diagnostic_continuation')
        mask=r.observables['quantitative_valid_mask']
        self.assertEqual(mask[0],1.);self.assertEqual(mask[-1],0.)
        self.assertTrue(np.all(np.diff(mask)<=0.))

    def test_failed_segment_retains_valid_archive_contract(self):
        from unittest.mock import patch
        from airvortex.numerics.thin_film import ThinFilmTrajectory
        c=SimulationConfig()
        c=replace(c,forward=replace(c.forward,model_branch='AV-FWD-TF-v1'))
        failed=ThinFilmTrajectory(np.array([0.]),np.full((1,64),1e-4),
            np.zeros(1),np.zeros(1),np.zeros(1),np.zeros(1),
            'numerical_failure',1,(0.,.001),'forced nonconvergent segment')
        with patch('airvortex.backends.forward_film.integrate_positive_thin_film',return_value=failed):
            r=ForwardTopologyBackend().run(c)
            self.assertEqual(r.status.value,'failed')
            self.assertEqual(r.metadata['quantitative_validity']['status'],'numerical_failure')
            np.testing.assert_array_equal(r.observables['quantitative_valid_mask'],0.)

if __name__=='__main__':
    unittest.main()
