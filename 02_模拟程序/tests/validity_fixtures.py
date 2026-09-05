"""Explicit synthetic identities for unit-test data, never production defaults."""
from __future__ import annotations

from dataclasses import replace
import hashlib

import numpy as np

from airvortex.core import GasModelConfig, SimulationResult, make_quantitative_validity


SYNTHETIC_THEORY_HASH = hashlib.sha256(b"analytic synthetic test signals; not an apparatus model").hexdigest()


def ideal_test_reservoir(*, until_s: float = 10.0, max_depth_m: float = 0.01) -> GasModelConfig:
    return GasModelConfig(
        regime="open_reservoir", evidence_kind="synthetic_assumption",
        evidence_source="synthetic prescribed zero-pressure-error reservoir, not measured apparatus data",
        evidence_valid_until_s=until_s,
        pressure_error_budget_pa=1.0,
        reservoir_pressure_error_bound_pa=0.0,
        reservoir_reference_pressure_pa=100.0,
        reservoir_max_funnel_depth_m=max_depth_m,
        reservoir_max_surface_slope=1.0,
    )


def synthetic_signal_result(*, first_invalid_time_s=None, **kwargs) -> SimulationResult:
    """Declare synthetic data used to test alignment/units, not a solver output.

    Call sites explicitly opt in.  Archive compatibility tests continue to use
    the real SimulationResult without a contract to exercise legacy rejection.
    """
    time = np.asarray(kwargs["time_s"], dtype=float)
    metadata = dict(kwargs.get("metadata", {}))
    observations = dict(kwargs["observables"])
    mask = np.ones(time.size, dtype=float) if first_invalid_time_s is None else (time < first_invalid_time_s).astype(float)
    observations["quantitative_valid_mask"] = mask
    valid_until = float(time[mask == 1.0][-1]) if np.any(mask == 1.0) else None
    metadata["quantitative_validity"] = make_quantitative_validity(
        model_branch="SYNTHETIC-ANALYTIC-SIGNAL",
        theory_sha256=SYNTHETIC_THEORY_HASH,
        implementation_id="unit-test-analytic-signal-v1",
        status="valid" if first_invalid_time_s is None else "diagnostic_continuation",
        valid_until_s=valid_until,
        first_invalid_time_s=first_invalid_time_s,
        termination_interval_s=None if first_invalid_time_s is None else (valid_until or 0.0, first_invalid_time_s),
        diagnostic_continuation=first_invalid_time_s is not None,
        reason="synthetic mathematical signals with explicitly known support",
    )
    return SimulationResult(**{**kwargs, "metadata": metadata, "observables": observations})


def with_synthetic_signal_validity(result: SimulationResult) -> SimulationResult:
    return synthetic_signal_result(
        backend=result.backend, time_s=result.time_s, observables=result.observables,
        metadata=result.metadata, fields=result.fields, events=result.events, status=result.status,
    )


def normalize_synthetic_signal_result(payload, mode):
    """Give GUI alignment fixtures an explicit mathematical-signal contract."""
    from airvortex.gui.model import normalize_simulation_result
    time = np.asarray(payload["time_s"], dtype=float)
    fixture = synthetic_signal_result(backend=mode, time_s=time,
                                      observables={"fixture": np.zeros(time.size)})
    key = "observables" if "observables" in payload else "signals"
    observations = dict(payload[key])
    observations["quantitative_valid_mask"] = np.ones(time.size, dtype=float)
    metadata = {**payload.get("metadata", {}),
                "quantitative_validity": fixture.metadata["quantitative_validity"]}
    return normalize_simulation_result({**payload, key: observations, "metadata": metadata}, mode)
