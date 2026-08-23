"""Widest-path 3D gas-channel topology diagnostics."""
from __future__ import annotations
import heapq, math
import numpy as np
from scipy.ndimage import binary_propagation, distance_transform_edt
from airvortex.numerics.interface3d import Topology3DDiagnostics

def _bar_adjacent(gas,grid):
    target=np.zeros(grid.shape,dtype=bool)
    target[:-1]|=gas[:-1]&grid.bar_mask[1:]; target[1:]|=gas[1:]&grid.bar_mask[:-1]
    target[:,:-1]|=gas[:,:-1]&grid.bar_mask[:,1:]; target[:,1:]|=gas[:,1:]&grid.bar_mask[:,:-1]
    target[:,:,:-1]|=gas[:,:,:-1]&grid.bar_mask[:,:,1:]; target[:,:,1:]|=gas[:,:,1:]&grid.bar_mask[:,:,:-1]
    return target

def _neighbours(idx,shape):
    i,j,k=idx; nx,ny,nz=shape
    if i: yield i-1,j,k
    if i+1<nx: yield i+1,j,k
    if j: yield i,j-1,k
    if j+1<ny: yield i,j+1,k
    if k: yield i,j,k-1
    if k+1<nz: yield i,j,k+1

def _widest(gas,target,grid):
    if not np.any(target): return 0.0
    capacity=distance_transform_edt(gas,sampling=(grid.dx,grid.dy,grid.dz))
    best=np.zeros(grid.shape); heap=[]
    for i,j in np.argwhere(gas[:,:,-1]):
        value=float(capacity[i,j,-1]); best[i,j,-1]=value; heapq.heappush(heap,(-value,int(i),int(j),grid.nz-1))
    while heap:
        neg,i,j,k=heapq.heappop(heap); value=-neg
        if value+1e-15<best[i,j,k]: continue
        if target[i,j,k]: return value
        for n in _neighbours((i,j,k),grid.shape):
            if not gas[n]: continue
            candidate=min(value,float(capacity[n]))
            if candidate>best[n]+1e-15:
                best[n]=candidate; heapq.heappush(heap,(-candidate,*n))
    return 0.0

def topology_diagnostics_widest_path_3d(phase,grid,*,interface_thickness_m,minimum_resolved_throat_cells,gas_threshold=0.5):
    gas=(np.asarray(phase,dtype=float)<gas_threshold)&grid.active_mask
    seed=np.zeros(grid.shape,dtype=bool); seed[:,:,-1]=gas[:,:,-1]
    connected=binary_propagation(seed,mask=gas)
    target=_bar_adjacent(connected,grid)
    bottleneck=_widest(connected,target,grid)
    touches=bool(np.any(target)); finite=bool(touches and bottleneck>0)
    required=max(minimum_resolved_throat_cells*grid.maximum_spacing_m,1.5*interface_thickness_m,2.0*grid.maximum_spacing_m)
    resolved=bool(finite and bottleneck>=required)
    if np.any(connected):
        gap=max(0.0,float(np.min(grid.bar_signed_distance_m[connected]))-0.5*math.sqrt(grid.dx**2+grid.dy**2+grid.dz**2))
    else: gap=math.inf
    isolated=gas&~connected
    # Contact area is kept as an auxiliary diagnostic; path bottleneck determines channel width.
    contact_area=float(np.sum(target)*min(grid.dx*grid.dy,grid.dx*grid.dz,grid.dy*grid.dz))
    return Topology3DDiagnostics(
        touches_bar=touches,
        finite_channel_candidate=finite,
        resolved_channel=resolved,
        bar_gas_contact_area_m2=contact_area,
        minimum_throat_radius_m=bottleneck,
        required_resolved_radius_m=required,
        minimum_gap_to_bar_m=gap,
        isolated_gas_volume_m3=float(np.sum(isolated)*grid.cell_volume_m3),
        top_connected_gas_volume_m3=float(np.sum(connected)*grid.cell_volume_m3),
        throat_radius_by_height_m=np.zeros(grid.nz,dtype=float),
        top_connected_gas_mask=connected,
    )
