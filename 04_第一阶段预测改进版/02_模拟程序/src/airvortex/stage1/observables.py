"""Canonicalise legacy backend outputs to the shared v3 prediction contract."""
from __future__ import annotations
import numpy as np
from airvortex.core import SimulationResult, Event
from .contracts import contract_document, EVENT_CONTRACT_VERSION, PredictionState

def _first(source,*keys):
    for key in keys:
        if key in source: return np.asarray(source[key],dtype=float)
    raise KeyError(keys)

def _events(result):
    output=[]; times=np.asarray(result.time_s,dtype=float)
    mapping={"gas_touches_bar":("geometric_touch",PredictionState.GEOMETRIC_TOUCH),"resolved_channel":("connected_resolved",PredictionState.CONNECTED_RESOLVED),"channel_reconnect":("connected_resolved",PredictionState.CONNECTED_RESOLVED),"channel_break":("broken_after_connection",PredictionState.BROKEN_AFTER_CONNECTION),"sustained_channel_numerical":("connected_sustained_numerical",PredictionState.CONNECTED_SUSTAINED_NUMERICAL),"first_target_touch":("geometric_touch",PredictionState.GEOMETRIC_TOUCH)}
    for event in result.events:
        kind,state=mapping.get(event.kind,(event.kind,None)); idx=int(np.searchsorted(times,event.time_s,side="left"))
        lower=0.0 if idx<=0 else float(times[min(idx-1,len(times)-1)]); upper=float(event.time_s if idx>=len(times) else times[idx])
        data=dict(event.data); data.update({"event_contract_version":EVENT_CONTRACT_VERSION,"source_event_kind":event.kind,"time_lower_s":lower,"time_upper_s":upper,"time_estimate_s":event.time_s,"bracket_width_s":max(0.0,upper-lower)})
        if state is not None: data["prediction_state"]=state.value
        output.append(Event(event.time_s,kind,event.message,data))
    return tuple(output)

def canonicalize_forward(result,config):
    source=result.observables; visible=_first(source,"visible_funnel_depth_m"); static=_first(source,"surface_depth_m","funnel_depth_m")
    obs={k:np.asarray(v) for k,v in source.items() if k not in {"funnel_depth_m","surface_depth_m","surface_depth","visible_funnel_depth_m"}}
    obs["central_depression_from_static_m"]=static; obs["visible_outer_to_center_depth_m"]=visible; obs["surface_depth_m"]=visible
    if "surface_profile_range_m" in source: obs["surface_height_range_m"]=np.asarray(source["surface_profile_range_m"])
    metadata=dict(result.metadata); metadata["backend_id"]="forward_stage1_predictive"; metadata["observable_contract"]=contract_document(obs); metadata["comparison_role"]="predictor_after_independent_calibration"
    return SimulationResult(result.backend,result.time_s,obs,result.fields,_events(result),metadata,result.status)

def canonicalize_hf3d(result,config):
    source=result.observables; visible=_first(source,"surface_depth_m","surface_depth"); static=_first(source,"nominal_central_depression_m")
    obs={k:np.asarray(v) for k,v in source.items() if k not in {"funnel_depth_m","surface_depth_m","surface_depth","nominal_central_depression_m","connection_candidate","connection_minimum_throat_radius_m"}}
    obs["central_depression_from_static_m"]=static; obs["visible_outer_to_center_depth_m"]=visible; obs["surface_depth_m"]=visible
    if "surface_profile_range_m" in source: obs["surface_height_range_m"]=np.asarray(source["surface_profile_range_m"])
    if "connection_candidate" in source: obs["connected_resolved"]=np.asarray(source["connection_candidate"],dtype=float)
    if "connection_minimum_throat_radius_m" in source: obs["channel_bottleneck_radius_m"]=np.asarray(source["connection_minimum_throat_radius_m"],dtype=float)
    metadata=dict(result.metadata); metadata["backend_id"]="high_fidelity_stage1_local_law"; metadata["observable_contract"]=contract_document(obs); metadata["comparison_role"]="independent_predictor_after_physical_GO"; metadata["physical_prediction_status"]="NO_GO_UNTIL_FROZEN_REFINEMENT_AND_EXPERIMENT_VALIDATION"
    return SimulationResult(result.backend,result.time_s,obs,result.fields,_events(result),metadata,result.status)
