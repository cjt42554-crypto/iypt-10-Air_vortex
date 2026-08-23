"""Face-flux CFL and phase-density-momentum consistent transport."""
from __future__ import annotations
from dataclasses import dataclass
import math
import numpy as np
from scipy.ndimage import binary_dilation

@dataclass(frozen=True, slots=True)
class FaceFluxes:
    x: np.ndarray
    y: np.ndarray
    z: np.ndarray
    top_outward_integral_flux: float

@dataclass(frozen=True, slots=True)
class TransportDiagnostics:
    advective_cfl: float
    phase_top_outward_flux_m3_s: float
    phase_conservation_residual_m3: float
    density_conservation_residual_kg: float
    phase_clip_absolute_volume_m3: float
    phase_repair_absolute_volume_m3: float
    momentum_remap_absolute_kg_m_s: float

def _masked_faces(u_x,u_y,u_z,grid):
    grid.require_face_velocity(u_x,u_y,u_z)
    return (np.where(grid.open_x,u_x,0.0),np.where(grid.open_y,u_y,0.0),np.where(grid.open_z,u_z,0.0))

def donor_cell_outflow_rate_s_inv(u_x,u_y,u_z,grid):
    ux,uy,uz=_masked_faces(u_x,u_y,u_z,grid)
    rate=(np.maximum(ux[1:],0)+np.maximum(-ux[:-1],0))/grid.dx
    rate+=(np.maximum(uy[:,1:],0)+np.maximum(-uy[:,:-1],0))/grid.dy
    rate+=(np.maximum(uz[:,:,1:],0)+np.maximum(-uz[:,:,:-1],0))/grid.dz
    return np.where(grid.active_mask,rate,0.0)

def maximum_donor_cell_rate_s_inv(u_x,u_y,u_z,grid):
    rate=donor_cell_outflow_rate_s_inv(u_x,u_y,u_z,grid)
    return float(np.max(rate[grid.active_mask]))

def advective_cfl_face_flux(u_x,u_y,u_z,grid,dt_s):
    if not math.isfinite(dt_s) or dt_s<0: raise ValueError("dt_s must be finite and non-negative")
    return dt_s*maximum_donor_cell_rate_s_inv(u_x,u_y,u_z,grid)

def _donor_flux(q,u_x,u_y,u_z,grid,top_value=0.0):
    ux,uy,uz=_masked_faces(u_x,u_y,u_z,grid)
    fx=np.zeros(grid.face_shape_x); fy=np.zeros(grid.face_shape_y); fz=np.zeros(grid.face_shape_z)
    fx[1:-1]=np.where(ux[1:-1]>=0,q[:-1],q[1:])*ux[1:-1]
    fy[:,1:-1]=np.where(uy[:,1:-1]>=0,q[:,:-1],q[:,1:])*uy[:,1:-1]
    fz[:,:,1:-1]=np.where(uz[:,:,1:-1]>=0,q[:,:,:-1],q[:,:,1:])*uz[:,:,1:-1]
    top=np.broadcast_to(np.asarray(top_value,dtype=float),(grid.nx,grid.ny))
    fz[:,:,-1]=np.where(uz[:,:,-1]>=0,q[:,:,-1],top)*uz[:,:,-1]
    fx*=grid.open_x; fy*=grid.open_y; fz*=grid.open_z
    return FaceFluxes(fx,fy,fz,float(np.sum(fz[:,:,-1])*grid.dx*grid.dy))

def _div(f,grid):
    value=(f.x[1:]-f.x[:-1])/grid.dx+(f.y[:,1:]-f.y[:,:-1])/grid.dy+(f.z[:,:,1:]-f.z[:,:,:-1])/grid.dz
    return np.where(grid.active_mask,value,0.0)

def _integral(q,grid): return grid.integrate(np.asarray(q,dtype=float))

def _repair_local(before,raw,grid,target):
    clipped=np.where(grid.active_mask,np.clip(raw,0,1),0.0)
    tol=64*np.finfo(float).eps
    seed=grid.active_mask&(((before>tol)&(before<1-tol))|(raw<0)|(raw>1))
    structure=np.zeros((3,3,3),dtype=bool); structure[1,1,1]=True; structure[0,1,1]=structure[2,1,1]=True; structure[1,0,1]=structure[1,2,1]=True; structure[1,1,0]=structure[1,1,2]=True
    target_sum=target/grid.cell_volume_m3
    base=float(np.sum(clipped[grid.active_mask])); residual=target_sum-base
    q=clipped.copy(); used=seed.copy()
    for layers in range(7):
        if layers: used=binary_dilation(used,structure=structure)&grid.active_mask
        remaining=residual
        for _ in range(32):
            capacity=np.where(used,1-q if remaining>0 else q,0.0); total=float(np.sum(capacity))
            if abs(remaining)<=1e-12*max(abs(target_sum),1): break
            if total<=np.finfo(float).tiny: break
            delta=min(1.0,abs(remaining)/total)*capacity
            if remaining>0: q+=delta; remaining-=float(np.sum(delta))
            else: q-=delta; remaining+=float(np.sum(delta))
            q=np.clip(q,0,1)
        if abs(remaining)<=1e-12*max(abs(target_sum),1):
            clip_abs=float(np.sum(np.abs(clipped-raw)[grid.active_mask])*grid.cell_volume_m3)
            repair_abs=float(np.sum(np.abs(q-clipped)[grid.active_mask])*grid.cell_volume_m3)
            return q,clip_abs,repair_abs
    raise FloatingPointError("interface-local bounded repair lacks capacity")

def advect_phase_density_momentum_consistently(phase,u_x_cell,u_y_cell,u_z_cell,u_x_face,u_y_face,u_z_face,grid,dt_s,*,liquid_density_kg_m3,gas_density_kg_m3,top_inflow_phase=0.0,top_inflow_u_x_m_s=0.0,top_inflow_u_y_m_s=0.0,top_inflow_u_z_m_s=0.0):
    if not math.isfinite(dt_s) or dt_s<=0: raise ValueError("dt_s must be finite and positive")
    alpha=np.asarray(phase,dtype=float); components=[np.asarray(u_x_cell,dtype=float),np.asarray(u_y_cell,dtype=float),np.asarray(u_z_cell,dtype=float)]
    ux,uy,uz=_masked_faces(u_x_face,u_y_face,u_z_face,grid)
    alpha_flux=_donor_flux(alpha,ux,uy,uz,grid,top_inflow_phase)
    raw_alpha=np.where(grid.active_mask,alpha-dt_s*_div(alpha_flux,grid),0.0)
    alpha_target=_integral(alpha,grid)-dt_s*alpha_flux.top_outward_integral_flux
    new_alpha,clip_abs,repair_abs=_repair_local(alpha,raw_alpha,grid,alpha_target)
    rho_l=float(liquid_density_kg_m3); rho_g=float(gas_density_kg_m3); drho=rho_l-rho_g
    old_rho=np.where(grid.active_mask,rho_g+drho*alpha,0.0)
    rho_flux=FaceFluxes(rho_g*ux+drho*alpha_flux.x,rho_g*uy+drho*alpha_flux.y,rho_g*uz+drho*alpha_flux.z,0.0)
    raw_rho=np.where(grid.active_mask,old_rho-dt_s*_div(rho_flux,grid),0.0)
    top_mass_flux=float(np.sum(rho_flux.z[:,:,-1])*grid.dx*grid.dy)
    expected_mass=_integral(old_rho,grid)-dt_s*top_mass_flux
    raw_momentum=[]
    top_components=(top_inflow_u_x_m_s,top_inflow_u_y_m_s,top_inflow_u_z_m_s)
    for component,top_component in zip(components,top_components):
        fx=np.zeros(grid.face_shape_x); fy=np.zeros(grid.face_shape_y); fz=np.zeros(grid.face_shape_z)
        fx[1:-1]=rho_flux.x[1:-1]*np.where(ux[1:-1]>=0,component[:-1],component[1:])
        fy[:,1:-1]=rho_flux.y[:,1:-1]*np.where(uy[:,1:-1]>=0,component[:,:-1],component[:,1:])
        fz[:,:,1:-1]=rho_flux.z[:,:,1:-1]*np.where(uz[:,:,1:-1]>=0,component[:,:,:-1],component[:,:,1:])
        top=np.broadcast_to(np.asarray(top_component,dtype=float),(grid.nx,grid.ny)); fz[:,:,-1]=rho_flux.z[:,:,-1]*np.where(uz[:,:,-1]>=0,component[:,:,-1],top)
        raw_momentum.append(np.where(grid.active_mask,old_rho*component-dt_s*_div(FaceFluxes(fx,fy,fz,0.0),grid),0.0))
    transported_velocity=[np.divide(p,raw_rho,out=np.zeros_like(p),where=grid.active_mask) for p in raw_momentum]
    new_rho=np.where(grid.active_mask,rho_g+drho*new_alpha,0.0); delta_rho=new_rho-raw_rho
    corrections=[delta_rho*v for v in transported_velocity]
    final_velocity=[np.divide(p+c,new_rho,out=np.zeros_like(p),where=grid.active_mask) for p,c in zip(raw_momentum,corrections)]
    momentum_remap=float(sum(np.sum(np.abs(c)[grid.active_mask])*grid.cell_volume_m3 for c in corrections))
    diag=TransportDiagnostics(
        advective_cfl=advective_cfl_face_flux(ux,uy,uz,grid,dt_s),
        phase_top_outward_flux_m3_s=alpha_flux.top_outward_integral_flux,
        phase_conservation_residual_m3=_integral(raw_alpha,grid)-alpha_target,
        density_conservation_residual_kg=_integral(raw_rho,grid)-expected_mass,
        phase_clip_absolute_volume_m3=clip_abs,
        phase_repair_absolute_volume_m3=repair_abs,
        momentum_remap_absolute_kg_m_s=momentum_remap,
    )
    return new_alpha,new_rho,final_velocity[0],final_velocity[1],final_velocity[2],diag
