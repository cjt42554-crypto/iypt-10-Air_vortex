"""Isolated predictive wrappers for the forward and HF3D backends."""
from __future__ import annotations
import math
import numpy as np
from airvortex.backends import forward_topology as _forward
from airvortex.backends import high_fidelity_3d as _hf
from airvortex.core import BackendMode, SimulationConfig, SimulationResult
from airvortex.numerics.interface3d import mixture_property_3d, surface_tension_acceleration_3d
from airvortex.numerics.momentum3d import newtonian_stress_acceleration_3d, relax_no_slip_boundaries_3d, rotating_frame_acceleration_3d, rotating_to_laboratory_velocity_3d
from .transport3d import advect_phase_density_momentum_consistently, advective_cfl_face_flux, maximum_donor_cell_rate_s_inv
from .cls3d import compress_phase_density_momentum_consistently
from .topology3d import topology_diagnostics_widest_path_3d
from .observables import canonicalize_forward, canonicalize_hf3d

class ForwardStage1Backend(_forward.ForwardTopologyBackend):
    mode=BackendMode.FORWARD
    backend_id="forward_stage1_predictive"
    display_name="F1.0 第一阶段预测合同版"
    def run(self,config,control=None,progress=None,**kwargs):
        if config.backend is not BackendMode.FORWARD: raise ValueError("requires forward backend")
        document=config.to_dict(); document["run"]["output_interval_s"]=min(config.run.output_interval_s,config.forward.integration_time_step_s); dense=SimulationConfig.from_dict(document)
        result=super().run(dense,control=control,progress=progress,**kwargs)
        # Keep dense event evidence. GUI rendering may downsample independently.
        return canonicalize_forward(result,config)

class HighFidelityStage1Backend(_hf.HighFidelityBackend):
    mode=BackendMode.HIGH_FIDELITY
    backend_id="high_fidelity_stage1_local_law"
    display_name="HF3D 第一阶段一致输运局部律"

    @staticmethod
    def _time_step_limits(state,config,grid):
        hf=config.high_fidelity; fluid=config.fluid
        face_rate=maximum_donor_cell_rate_s_inv(state.u_x_face,state.u_y_face,state.u_z_face,grid)
        rpm_now,slope_now=_hf._drive_rpm_and_slope(config,state.time_s); rpm_trial,_=_hf._drive_rpm_and_slope(config,state.time_s+hf.time_step_s)
        omega_bound=max(abs(_hf._rpm_to_omega(rpm_now)),abs(_hf._rpm_to_omega(rpm_trial))); omega_dot=abs(_hf._rpm_to_omega(slope_now)); wall_rate=omega_bound*grid.radius_m*(1/grid.dx+1/grid.dy); rate_bound=max(face_rate,wall_rate)
        dt_adv=math.inf if rate_bound<=np.finfo(float).tiny else hf.cfl_limit/rate_bound
        nu=max(fluid.liquid_dynamic_viscosity_pa_s/fluid.liquid_density_kg_m3,fluid.gas_dynamic_viscosity_pa_s/fluid.gas_density_kg_m3); diff=2*nu*(1/grid.dx**2+1/grid.dy**2+1/grid.dz**2); dt_vis=0.45/diff
        h=grid.minimum_spacing_m; dt_cap=0.20*math.sqrt(fluid.gas_density_kg_m3*h**3/(math.pi*fluid.surface_tension_n_m)); ux,uy,uz=grid.faces_to_cells(state.u_x_face,state.u_y_face,state.u_z_face); speed=float(np.max(np.sqrt(ux*ux+uy*uy+uz*uz)[grid.active_mask])); source=fluid.gravity_m_s2+omega_bound**2*grid.radius_m+omega_dot*grid.radius_m+2*omega_bound*speed; dt_source=math.sqrt(hf.cfl_limit*h/max(source,np.finfo(float).tiny)); dt_rot=math.inf if omega_bound<=np.finfo(float).tiny else hf.maximum_rotation_increment_rad/omega_bound
        stable=min(hf.time_step_s,dt_adv,dt_vis,dt_cap,dt_source,dt_rot)
        return stable,{"advective_face_outflow_rate_s_inv":rate_bound,"viscous_rate_s_inv":diff,"capillary_dt_s":dt_cap,"source_dt_s":dt_source,"rotation_dt_s":dt_rot}

    def advance_one_step(self,state,config,grid,projection,dt_s,*,expected_liquid_volume_m3):
        hf=config.high_fidelity; fluid=config.fluid; old_phase=state.phase; old_ux,old_uy,old_uz=grid.faces_to_cells(state.u_x_face,state.u_y_face,state.u_z_face)
        midpoint=state.time_s+0.5*dt_s; rpm_mid,rpm_slope=_hf._drive_rpm_and_slope(config,midpoint); omega_mid=_hf._rpm_to_omega(rpm_mid); omega_dot=_hf._rpm_to_omega(rpm_slope); top_x=omega_mid*grid.y_cell[:,:,-1]; top_y=-omega_mid*grid.x_cell[:,:,-1]
        phase,new_density,ux,uy,uz,transport=advect_phase_density_momentum_consistently(old_phase,old_ux,old_uy,old_uz,state.u_x_face,state.u_y_face,state.u_z_face,grid,dt_s,liquid_density_kg_m3=fluid.liquid_density_kg_m3,gas_density_kg_m3=fluid.gas_density_kg_m3,top_inflow_phase=0.0,top_inflow_u_x_m_s=top_x,top_inflow_u_y_m_s=top_y,top_inflow_u_z_m_s=0.0)
        clip_abs=transport.phase_clip_absolute_volume_m3; repair_abs=transport.phase_repair_absolute_volume_m3; next_step=state.step+1
        if hf.interface_compression_interval>0 and next_step%hf.interface_compression_interval==0 and hf.interface_compression_pseudo_cfl>0:
            phase,new_density,ux,uy,uz,cls=compress_phase_density_momentum_consistently(phase,new_density,ux,uy,uz,grid,hf.interface_thickness_cells*grid.minimum_spacing_m,pseudo_cfl=hf.interface_compression_pseudo_cfl,liquid_density_kg_m3=fluid.liquid_density_kg_m3,gas_density_kg_m3=fluid.gas_density_kg_m3); clip_abs+=cls["clip_absolute_volume_m3"]; repair_abs+=cls["repair_absolute_volume_m3"]
        viscosity=mixture_property_3d(phase,fluid.liquid_dynamic_viscosity_pa_s,fluid.gas_dynamic_viscosity_pa_s); vx,vy,vz,vdiag=newtonian_stress_acceleration_3d(ux,uy,uz,viscosity,new_density,grid); sx,sy,sz,_,sdiag=surface_tension_acceleration_3d(phase,new_density,grid,fluid.surface_tension_n_m,contact_angles_deg={_hf.SIDE_WALL:hf.side_contact_angle_deg,_hf.FLOOR_WALL:hf.floor_contact_angle_deg,_hf.BAR_WALL:hf.bar_contact_angle_deg},smoothing_cells=hf.surface_tension_smoothing_cells)
        ux+=dt_s*vx; uy+=dt_s*vy; uz+=dt_s*vz; rpm_end,_=_hf._drive_rpm_and_slope(config,state.time_s+dt_s); omega_end=_hf._rpm_to_omega(rpm_end); ux,uy,uz,bdiag=relax_no_slip_boundaries_3d(ux,uy,uz,new_density,grid,omega_end,dt_s,relaxation_time_s=hf.boundary_relaxation_time_s,layer_cells=hf.boundary_layer_cells); fx,fy,fz=rotating_frame_acceleration_3d(ux,uy,grid,omega_mid,omega_dot); ux+=dt_s*(sx+fx); uy+=dt_s*(sy+fy); uz+=dt_s*(sz+fz-fluid.gravity_m_s2)
        uxf,uyf,uzf=grid.cells_to_faces(ux,uy,uz); uxf,uyf,uzf,pressure,pdiag=projection.project(uxf,uyf,uzf,new_density,dt_s,initial_pressure_pa=state.pressure_pa); pux,puy,puz=grid.faces_to_cells(uxf,uyf,uzf); post_cfl=advective_cfl_face_flux(uxf,uyf,uzf,grid,dt_s)
        if post_cfl>hf.cfl_limit*(1+1e-9): raise FloatingPointError("HF3D face-flux CFL exceeds configured limit")
        volume=grid.integrate(phase); expected_after=expected_liquid_volume_m3-dt_s*transport.phase_top_outward_flux_m3_s; labx,laby,labz=rotating_to_laboratory_velocity_3d(pux,puy,puz,grid,omega_end); kinetic_lab=0.5*grid.integrate(new_density*(labx*labx+laby*laby+labz*labz)); kinetic_rot=0.5*grid.integrate(new_density*(pux*pux+puy*puy+puz*puz)); angular=grid.integrate(new_density*(grid.x_cell*laby-grid.y_cell*labx)); topology=topology_diagnostics_widest_path_3d(phase,grid,interface_thickness_m=hf.interface_thickness_cells*grid.minimum_spacing_m,minimum_resolved_throat_cells=hf.minimum_resolved_throat_cells)
        state.time_s+=dt_s; state.step=next_step; state.bar_angle_rad=_hf._integrated_bar_angle_rad(config,state.time_s); state.u_x_face=uxf; state.u_y_face=uyf; state.u_z_face=uzf; state.phase=phase; state.pressure_pa=pressure
        nu=max(fluid.liquid_dynamic_viscosity_pa_s/fluid.liquid_density_kg_m3,fluid.gas_dynamic_viscosity_pa_s/fluid.gas_density_kg_m3); viscous_cfl=2*nu*dt_s*(1/grid.dx**2+1/grid.dy**2+1/grid.dz**2); cap_dt=0.20*math.sqrt(fluid.gas_density_kg_m3*grid.minimum_spacing_m**3/(math.pi*fluid.surface_tension_n_m))
        return _hf.HF3DStepDiagnostics(dt_s,rpm_end,omega_end,omega_dot,post_cfl,viscous_cfl,dt_s/cap_dt,abs(omega_mid)*dt_s,pdiag.divergence_before_s_inv,pdiag.divergence_after_s_inv,pdiag.pressure_relative_residual,pdiag.iterations,volume,expected_after,volume-expected_after,transport.phase_top_outward_flux_m3_s,clip_abs,repair_abs,kinetic_lab,kinetic_rot,angular,vdiag.viscous_dissipation_w,sdiag.maximum_acceleration_m_s2,vdiag.maximum_viscous_acceleration_m_s2,bdiag.maximum_relaxation_acceleration_m_s2,bdiag.rotating_frame_kinetic_energy_change_j,topology)

    def run(self,config,control=None,progress=None):
        old_topology=_hf.topology_diagnostics_3d; old_cfl=_hf.advective_cfl; _hf.topology_diagnostics_3d=topology_diagnostics_widest_path_3d; _hf.advective_cfl=advective_cfl_face_flux
        try: result=super().run(config,control=control,progress=progress)
        finally: _hf.topology_diagnostics_3d=old_topology; _hf.advective_cfl=old_cfl
        result=canonicalize_hf3d(result,config); metadata=dict(result.metadata); metadata["stage1_numeric_changes"]={"cfl":"donor-cell face outflow coefficient","transport":"shared phase-density-momentum face flux","topology":"3D widest-path bottleneck","capillary_balance":"STILL_REQUIRES_STATIC_DROP_AND_CAPILLARY_WAVE_VALIDATION"}
        return SimulationResult(result.backend,result.time_s,result.observables,result.fields,result.events,metadata,result.status)

ForwardTheoryBackend=ForwardStage1Backend
HighFidelityBackend=HighFidelityStage1Backend
