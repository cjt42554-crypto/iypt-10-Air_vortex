"""Conservative CLS pseudo-transport coupled to density and momentum."""
from __future__ import annotations
import math
import numpy as np
from airvortex.numerics.interface3d import masked_gradient_3d
from .transport3d import FaceFluxes, _div, _integral, _repair_local

def compress_phase_density_momentum_consistently(phase,density,u_x,u_y,u_z,grid,interface_thickness_m,*,pseudo_cfl,liquid_density_kg_m3,gas_density_kg_m3):
    c=np.asarray(phase,dtype=float); rho=np.asarray(density,dtype=float); velocities=[np.asarray(u_x,dtype=float),np.asarray(u_y,dtype=float),np.asarray(u_z,dtype=float)]
    gx,gy,gz=masked_gradient_3d(c,grid,top_dirichlet=0.0); mag=np.sqrt(gx*gx+gy*gy+gz*gz); floor=1e-12/grid.minimum_spacing_m
    nx,ny,nz=gx/np.maximum(mag,floor),gy/np.maximum(mag,floor),gz/np.maximum(mag,floor)
    epsilon=interface_thickness_m/(2.0*math.log(9.0)); fx=np.zeros(grid.face_shape_x); fy=np.zeros(grid.face_shape_y); fz=np.zeros(grid.face_shape_z)
    cf=0.5*(c[:-1]+c[1:]); nf=0.5*(nx[:-1]+nx[1:]); fx[1:-1]=cf*(1-cf)*nf-epsilon*(c[1:]-c[:-1])/grid.dx
    cf=0.5*(c[:,:-1]+c[:,1:]); nf=0.5*(ny[:,:-1]+ny[:,1:]); fy[:,1:-1]=cf*(1-cf)*nf-epsilon*(c[:,1:]-c[:,:-1])/grid.dy
    cf=0.5*(c[:,:,:-1]+c[:,:,1:]); nf=0.5*(nz[:,:,:-1]+nz[:,:,1:]); fz[:,:,1:-1]=cf*(1-cf)*nf-epsilon*(c[:,:,1:]-c[:,:,:-1])/grid.dz
    fx*=grid.open_x; fy*=grid.open_y; fz[:,:,-1]=0.0; fz*=grid.open_z
    phase_flux=FaceFluxes(fx,fy,fz,0.0); pseudo_dt=pseudo_cfl*grid.minimum_spacing_m
    raw=np.where(grid.active_mask,c-pseudo_dt*_div(phase_flux,grid),0.0); target=_integral(c,grid); repaired,clip_abs,repair_abs=_repair_local(c,raw,grid,target)
    drho=float(liquid_density_kg_m3)-float(gas_density_kg_m3); rho_flux=FaceFluxes(drho*fx,drho*fy,drho*fz,0.0); raw_rho=np.where(grid.active_mask,rho-pseudo_dt*_div(rho_flux,grid),0.0)
    raw_momentum=[]
    for v in velocities:
        mfx=np.zeros_like(fx); mfy=np.zeros_like(fy); mfz=np.zeros_like(fz)
        mfx[1:-1]=rho_flux.x[1:-1]*np.where(fx[1:-1]>=0,v[:-1],v[1:]); mfy[:,1:-1]=rho_flux.y[:,1:-1]*np.where(fy[:,1:-1]>=0,v[:,:-1],v[:,1:]); mfz[:,:,1:-1]=rho_flux.z[:,:,1:-1]*np.where(fz[:,:,1:-1]>=0,v[:,:,:-1],v[:,:,1:])
        raw_momentum.append(np.where(grid.active_mask,rho*v-pseudo_dt*_div(FaceFluxes(mfx,mfy,mfz,0.0),grid),0.0))
    transported=[np.divide(p,raw_rho,out=np.zeros_like(p),where=grid.active_mask) for p in raw_momentum]
    new_rho=np.where(grid.active_mask,float(gas_density_kg_m3)+drho*repaired,0.0); delta=new_rho-raw_rho; corrections=[delta*v for v in transported]
    final=[np.divide(p+d,new_rho,out=np.zeros_like(p),where=grid.active_mask) for p,d in zip(raw_momentum,corrections)]
    return repaired,new_rho,final[0],final[1],final[2],{
        "clip_absolute_volume_m3":clip_abs,
        "repair_absolute_volume_m3":repair_abs,
        "momentum_remap_absolute_kg_m_s":float(sum(np.sum(np.abs(d)[grid.active_mask])*grid.cell_volume_m3 for d in corrections)),
        "pseudo_time_s":pseudo_dt,
    }
