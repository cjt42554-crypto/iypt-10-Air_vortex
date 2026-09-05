#!/usr/bin/env python3
"""Bounded linear-SH moving-surface probe; retains the energy-target failure."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import numpy as np
from scipy.interpolate import CubicSpline
from scipy.linalg import expm
from airvortex.core import SimulationConfig, GasModelConfig
from airvortex.io.config_io import config_sha256
from airvortex.backends.forward_topology import ForwardTopologyBackend
from airvortex.backends.forward_stokes import ForwardStokesBackend,PrescribedCovariance
from airvortex.numerics.axisymmetric import AxisymmetricGrid
from airvortex.numerics.stokes_axisymmetric import AxisymmetricStokesOperator

HERE=Path(__file__).resolve().parent

def config(n,dt=.001):
    c=SimulationConfig()
    return replace(c,
        forward=replace(c.forward,model_branch='AV-FWD-SH-v1'),
        drive=replace(c.drive,initial_rpm=0.,target_rpm=0.,ramp_duration_s=0.),
        # A separate synthetic high-viscosity mathematical case, NOT a change
        # to a water apparatus, its parameters, or its qualification limits.
        fluid=replace(c.fluid,liquid_dynamic_viscosity_pa_s=100.),
        gas=GasModelConfig(regime='open_reservoir',evidence_kind='synthetic_assumption',
            evidence_source='synthetic prescribed open atmosphere; not device data',
            evidence_valid_until_s=1.,pressure_error_budget_pa=1.,
            reservoir_pressure_error_bound_pa=0.,reservoir_reference_pressure_pa=100.,
            reservoir_max_funnel_depth_m=.01,reservoir_max_surface_slope=1.),
        stokes=replace(c.stokes,radial_cells=n,axial_cells=n,time_step_s=dt,
            initial_surface_mode=1,initial_surface_amplitude_m=1e-6),
        run=replace(c.run,duration_s=.02,output_interval_s=.01))

def main():
    rows=[];profiles=[]; radii=[]
    for n in [8,16,32,64]:
        c=config(n); r=ForwardTopologyBackend().run(c)
        energy=r.observables['surface_energy_j']; error=float(r.observables['energy_balance_residual_j'][-1]/energy[0])
        rows.append({'n':n,'config_sha256':config_sha256(c),
            'completed_time_s':float(r.time_s[-1]),
            'volume_error_m3':float(np.max(abs(r.observables['volume_error_m3']))),
            'energy_relative_error':error,'energy_target':1e-6,
            'energy_status':'PASS' if abs(error)<=1e-6 else 'FAIL',
            'maximum_meridional_reynolds':float(np.max(r.observables['meridional_reynolds'])),
            'maximum_unsteady_stokes':float(np.max(r.observables['unsteady_stokes']))})
        profiles.append(r.observables['surface_profile_m'][-1]-c.container.liquid_depth_m)
        radii.append(r.fields['radius_m'])
    interpolator=CubicSpline(radii[-1],profiles[-1])
    spatial=[float(np.linalg.norm(x-interpolator(rad))/(np.sqrt(x.size)*1e-6+np.linalg.norm(interpolator(rad)))) for x,rad in zip(profiles[:-1],radii[:-1])]
    # Exact matrix exponential of the same finite-cylinder semidiscrete operator
    # isolates the RK4 time error. This is not an independent spatial reference.
    c=config(12); c=replace(c,run=replace(c.run,duration_s=.5,output_interval_s=.25)); g=AxisymmetricGrid(12,12,c.container.radius_m,c.container.liquid_depth_m)
    op=AxisymmetricStokesOperator(g,c.fluid.liquid_dynamic_viscosity_pa_s)
    cov=PrescribedCovariance(c,g); area=np.pi*np.diff(g.r_faces**2)
    eta=ForwardStokesBackend._initial_surface(c,g,area)
    columns=[]
    for j in range(12):
        basis=np.eye(12)[:,j]
        value=ForwardStokesBackend._instantaneous(c,g,op,cov,0.,0.,basis,area,previous_solution=None,previous_elapsed=None)
        columns.append(value.eta_rate_m_s)
    matrix=np.column_stack(columns)
    exact=expm(c.run.duration_s*matrix)@eta
    time=[]
    for dt in [.125,.0625,.03125]:
        cc=replace(c,stokes=replace(c.stokes,time_step_s=dt))
        r=ForwardTopologyBackend().run(cc)
        measured=r.observables['surface_profile_m'][-1]-cc.container.liquid_depth_m
        time.append({'dt_s':dt,'error':float(np.linalg.norm(measured-exact)/(np.sqrt(12)*1e-6+np.linalg.norm(exact)))})
    report={'scope':'synthetic linear reference cylinder only; nonlinear moving domain NOT_IMPLEMENTED',
        'physical_inputs':config(8).to_dict(),'moving_space':rows,'surface_errors_vs_64':spatial,
        'time_vs_semidiscrete_exponential':time,
        'status':'PARTIAL_FIXED_DOMAIN_ONLY__MOVING_ENERGY_QUALIFICATION_FAILED',
        'consequence':'Do not claim 10-C3 or full SH completion. Energy and moving-domain work remain research backlog.',
        'source_hashes':{str(p.relative_to(HERE.parents[3])):hashlib.sha256(p.read_bytes()).hexdigest() for p in [
            HERE.parents[3]/'02_模拟程序/src/airvortex/backends/forward_stokes.py',
            HERE.parents[3]/'02_模拟程序/src/airvortex/numerics/stokes_axisymmetric.py']}}
    (HERE/'moving_reference.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'status':report['status'],'moving_space':rows,'surface_errors_vs_64':spatial,'time':time},ensure_ascii=False))

if __name__=='__main__': main()
