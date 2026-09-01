#!/usr/bin/env python3
"""Construct one exact P0-P2 HF3D case in a fresh process and advance zero steps."""

from __future__ import annotations

import argparse
import gc
import hashlib
import importlib.metadata
import math
import os
import platform
import sys
import time
from dataclasses import fields
from pathlib import Path
from typing import Any

from p0p2_contract import (
    BENCHMARKS,
    CONTRACT_ID,
    CONTRACT_PATH,
    EFFECTIVE_CONFIG_ROOT,
    FORMAL_ROOT,
    FORMAL_SOURCE,
    FORMAL_SOURCE_SHA256,
    SCHEDULE_ID,
    P0P2Error,
    assert_runtime_input_separation,
    canonical_sha256,
    effective_config,
    fixed_schedule,
    load_contract,
    pretty_json_bytes,
    relative,
    source_config_path,
    strict_json,
    validate_effective_payload,
)

sys.dont_write_bytecode = True
if str(FORMAL_SOURCE) not in sys.path:
    sys.path.insert(0, str(FORMAL_SOURCE))

# The formal package is imported before NumPy/SciPy/CuPy so its early numerical
# thread policy is the one actually applied by the exact production stack.
import airvortex  # noqa: F401
import cupy as cp  # type: ignore[import-untyped]
import numpy as np
import scipy  # type: ignore[import-untyped]
from airvortex._hardware import NUMERICAL_THREAD_POLICY
from airvortex.backends.high_fidelity_3d import HighFidelityBackend
from airvortex.core import Event, SimulationConfig
from airvortex.hf3d.events import TopologyEventTracker
from airvortex.hf3d.pressure import make_pressure_projection
from airvortex.hf3d.recording import (
    FrameRecorder,
    StepDiagnosticsBuffer,
)
from airvortex.io.archive import source_tree_identity


def _process_start_ticks() -> int:
    raw = (Path("/proc") / str(os.getpid()) / "stat").read_text(encoding="utf-8")
    return int(raw.rsplit(")", 1)[1].split()[19])


def _array_identity(value: np.ndarray[Any, Any]) -> dict[str, Any]:
    array = np.asarray(value)
    payload = np.ascontiguousarray(array).tobytes(order="C")
    numeric = array.dtype.kind in "biufc"
    finite = bool(np.isfinite(array).all()) if numeric else True
    result: dict[str, Any] = {
        "shape": list(array.shape),
        "dtype": array.dtype.str,
        "c_contiguous": bool(array.flags.c_contiguous),
        "byte_length": len(payload),
        "sha256_c_order_bytes": hashlib.sha256(payload).hexdigest(),
        "all_finite": finite,
    }
    if array.size and numeric:
        result["minimum"] = float(np.min(array))
        result["maximum"] = float(np.max(array))
    return result


def _diagnostic_summary(diagnostics: Any) -> dict[str, Any]:
    scalars: dict[str, Any] = {}
    for field in fields(diagnostics):
        if field.name == "topology":
            continue
        value = getattr(diagnostics, field.name)
        if isinstance(value, np.generic):
            value = value.item()
        scalars[field.name] = value
    topology = diagnostics.topology
    topology_scalars = {
        field.name: getattr(topology, field.name)
        for field in fields(topology)
        if field.name not in {"throat_radius_by_height_m", "top_connected_gas_mask"}
    }
    return {
        "scalars": scalars,
        "topology": {
            "scalars": topology_scalars,
            "throat_radius_by_height_m": _array_identity(
                topology.throat_radius_by_height_m
            ),
            "top_connected_gas_mask": _array_identity(topology.top_connected_gas_mask),
        },
    }


def _all_scalars_finite(value: object) -> bool:
    if isinstance(value, bool | int | str | type(None)):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, dict):
        return all(_all_scalars_finite(item) for item in value.values())
    if isinstance(value, list | tuple):
        return all(_all_scalars_finite(item) for item in value)
    return False


def _versions() -> dict[str, str]:
    return {
        "python": platform.python_version(),
        "numpy": np.__version__,
        "scipy": scipy.__version__,
        "cupy": cp.__version__,
        "cupy-cuda13x": importlib.metadata.version("cupy-cuda13x"),
    }


def run_probe(benchmark: str) -> dict[str, Any]:
    if benchmark not in BENCHMARKS:
        raise P0P2Error(f"unknown benchmark: {benchmark}")
    started_ns = time.perf_counter_ns()
    source_identity = source_tree_identity(FORMAL_ROOT)
    contract = load_contract()
    schedule = fixed_schedule()
    computed_payload, computed_identity = effective_config(benchmark)
    effective_path = EFFECTIVE_CONFIG_ROOT / f"P0P2_{benchmark}.json"
    generated_payload = strict_json(effective_path)
    if not isinstance(generated_payload, dict):
        raise P0P2Error("generated effective config must be one object")
    if generated_payload != computed_payload:
        raise P0P2Error("generated effective config differs from deterministic payload")
    payload = generated_payload
    config_identity = validate_effective_payload(benchmark, payload)
    if config_identity != computed_identity:
        raise P0P2Error("generated effective config identity differs from contract")
    runtime_input_gates = assert_runtime_input_separation(
        [CONTRACT_PATH, FORMAL_SOURCE, source_config_path(benchmark), effective_path]
    )
    contract_binding = contract.get("effective_configurations", {}).get(benchmark)
    if not (
        contract.get("contract_id") == CONTRACT_ID
        and contract.get("fixed_schedule", {}).get("schedule_id") == SCHEDULE_ID
        and contract.get("fixed_schedule", {}).get("canonical_schedule_sha256")
        == schedule["canonical_schedule_sha256"]
        and contract_binding == config_identity
    ):
        raise P0P2Error("probe contract/effective-config binding failed")

    config = SimulationConfig.from_dict(payload)
    round_trip = config.to_dict()
    backend = HighFidelityBackend()
    pool = cp.get_default_memory_pool()
    pinned_pool = cp.get_default_pinned_memory_pool()
    pool_before = {
        "used_bytes": int(pool.used_bytes()),
        "total_bytes": int(pool.total_bytes()),
    }
    free_before, total_memory = cp.cuda.runtime.memGetInfo()
    grid = backend.make_grid(config)
    projection, accelerator = make_pressure_projection(config, grid)
    cp.cuda.runtime.deviceSynchronize()
    free_after_projection, _ = cp.cuda.runtime.memGetInfo()
    state = backend.initialise_state(config, grid)
    initial_volume = backend._initial_liquid_volume(config, grid)
    diagnostics = backend._zero_step_diagnostics(state, config, grid, initial_volume)
    recorder = FrameRecorder(config, grid, state, initial_volume)
    diagnostics_buffer = StepDiagnosticsBuffer(initial_volume)
    events: list[Event] = []
    tracker = TopologyEventTracker(config, events)
    recorder.record_frame(diagnostics)
    tracker.initialise(diagnostics.topology)

    state_arrays = {
        "u_x_face": _array_identity(state.u_x_face),
        "u_y_face": _array_identity(state.u_y_face),
        "u_z_face": _array_identity(state.u_z_face),
        "phase": _array_identity(state.phase),
        "pressure_pa": _array_identity(state.pressure_pa),
    }
    grid_arrays = {
        "active_mask": _array_identity(grid.active_mask),
        "bar_mask": _array_identity(grid.bar_mask),
    }
    diagnostic_summary = _diagnostic_summary(diagnostics)
    frame_identities = {
        "phase": _array_identity(recorder.phase_frames[0]),
        "ur": _array_identity(recorder.ur_frames[0]),
        "uz": _array_identity(recorder.uz_frames[0]),
        "utheta": _array_identity(recorder.utheta_frames[0]),
        "pressure": _array_identity(recorder.pressure_frames[0]),
    }
    properties = cp.cuda.runtime.getDeviceProperties(0)
    device_name_raw = properties.get("name", b"")
    device_name = (
        device_name_raw.decode("utf-8", errors="strict")
        if isinstance(device_name_raw, bytes)
        else str(device_name_raw)
    )
    versions = _versions()
    gates = {
        "formal_source_tree_exact": (
            source_identity.get("available") is True
            and source_identity.get("value") == FORMAL_SOURCE_SHA256
        ),
        "formal_config_from_dict_and_exact_round_trip": round_trip == payload,
        "effective_config_canonical_hash_exact": (
            canonical_sha256(round_trip)
            == BENCHMARKS[benchmark]["effective_config_sha256"]
        ),
        "generated_effective_config_exact": config_identity == computed_identity,
        "runtime_inputs_separated_from_v1_and_e0": all(runtime_input_gates.values()),
        "duration_ramp_and_output_schedule_compatible": (
            config.run.duration_s == config.drive.ramp_duration_s == 0.005
            and config.run.output_interval_s == 0.001
            and config.drive.actual_speed_schedule_time_s == ()
        ),
        "actual_cuda_without_fallback": (
            accelerator.get("selected") == "cuda"
            and accelerator.get("fallback") is False
            and config.high_fidelity.pressure_solver_device == "cuda"
        ),
        "grid_constructed_exact_shape": tuple(grid.shape)
        == (
            config.high_fidelity.radial_cells,
            config.high_fidelity.azimuthal_cells,
            config.high_fidelity.axial_cells,
        ),
        "pressure_engine_constructed_on_device": (
            projection.__class__.__name__ == "CuPyVariableDensityProjection3D"
            and free_after_projection < free_before
        ),
        "initial_state_zero_steps": (
            state.time_s == 0.0
            and state.step == 0
            and diagnostics.dt_s == 0.0
            and diagnostics.time_step_limiter == "initial_state"
        ),
        "state_and_grid_arrays_finite": all(
            item["all_finite"]
            for item in [*state_arrays.values(), *grid_arrays.values()]
        ),
        "zero_step_diagnostics_finite": _all_scalars_finite(diagnostic_summary),
        "initial_frame_recorded_exactly_once": (
            recorder.times == [0.0]
            and len(recorder.phase_frames)
            == len(recorder.ur_frames)
            == len(recorder.uz_frames)
            == len(recorder.utheta_frames)
            == len(recorder.pressure_frames)
            == 1
            and all(len(values) == 1 for values in recorder.observable_lists.values())
        ),
        "diagnostic_buffer_empty_no_accepted_step": all(
            len(value) == 0
            for value in vars(diagnostics_buffer).values()
            if isinstance(value, list)
        ),
        "event_tracker_initialised_without_events": events == [],
        "expected_gpu_identity": device_name == "NVIDIA GeForce RTX 5060 Laptop GPU",
        "expected_toolchain_versions": versions
        == {
            "python": "3.12.3",
            "numpy": "2.5.2",
            "scipy": "1.17.1",
            "cupy": "14.2.0",
            "cupy-cuda13x": "14.2.0",
        },
        "hf3d_time_steps_advanced_exactly_zero": state.step == 0,
    }

    report: dict[str, Any] = {
        "schema_version": 1,
        "status": "HF3D_P0P2_EXACT_CASE_ZERO_STEP_PROBE_PASS",
        "gate_pass": all(gates.values()),
        "gates": gates,
        "benchmark_id": benchmark,
        "process_identity": {
            "pid": os.getpid(),
            "process_start_ticks": _process_start_ticks(),
            "python_executable": str(Path(sys.executable).resolve()),
            "platform": {
                "system": platform.system(),
                "release": platform.release(),
                "machine": platform.machine(),
            },
        },
        "runtime_inputs": {
            "contract": relative(CONTRACT_PATH),
            "source_config": relative(source_config_path(benchmark)),
            "generated_effective_config": relative(effective_path),
            "formal_source_tree": relative(FORMAL_SOURCE),
            "v1_raw_or_e0_snapshot_used": False,
            "separation_gates": runtime_input_gates,
        },
        "config_identity": config_identity,
        "formal_source_identity": source_identity,
        "toolchain": {
            "versions": versions,
            "numerical_thread_policy": NUMERICAL_THREAD_POLICY.to_dict(),
            "cuda_runtime_version": int(cp.cuda.runtime.runtimeGetVersion()),
            "cuda_driver_version": int(cp.cuda.runtime.driverGetVersion()),
            "device_name": device_name,
            "compute_capability": (
                f"{int(properties.get('major', 0))}.{int(properties.get('minor', 0))}"
            ),
            "total_global_memory_bytes": int(properties.get("totalGlobalMem", 0)),
        },
        "grid": {
            "shape": list(grid.shape),
            "minimum_spacing_m": float(grid.minimum_spacing_m),
            "maximum_spacing_m": float(grid.maximum_spacing_m),
            "active_cell_count": int(np.count_nonzero(grid.active_mask)),
            "bar_cell_count": int(np.count_nonzero(grid.bar_mask)),
            "arrays": grid_arrays,
        },
        "pressure_engine": {
            "class": (
                f"{projection.__class__.__module__}.{projection.__class__.__qualname__}"
            ),
            "accelerator": dict(accelerator),
            "cuda_memory": {
                "free_before_projection_bytes": int(free_before),
                "free_after_projection_bytes": int(free_after_projection),
                "total_bytes": int(total_memory),
                "allocated_by_projection_bytes_lower_bound": int(
                    free_before - free_after_projection
                ),
            },
        },
        "initial_state": {
            "time_s": state.time_s,
            "step": state.step,
            "bar_angle_rad": state.bar_angle_rad,
            "initial_liquid_volume_m3": initial_volume,
            "arrays": state_arrays,
        },
        "zero_step_diagnostics": diagnostic_summary,
        "initial_frame": {
            "times_s": list(recorder.times),
            "observable_names": sorted(recorder.observable_lists),
            "observable_count": len(recorder.observable_lists),
            "arrays": frame_identities,
        },
        "hf3d_time_steps_advanced": 0,
        "elapsed_wall_s_before_teardown": (time.perf_counter_ns() - started_ns) / 1.0e9,
    }

    # Drop every runtime owner, then release CuPy cache blocks.  The process
    # exits immediately afterward, but the explicit check makes teardown part
    # of the zero-step evidence rather than an assumption.
    del tracker
    del events
    del diagnostics_buffer
    del recorder
    del diagnostics
    del state
    del projection
    del grid
    del backend
    del config
    gc.collect()
    cp.cuda.runtime.deviceSynchronize()
    pool.free_all_blocks()
    pinned_pool.free_all_blocks()
    cp.cuda.runtime.deviceSynchronize()
    free_after_teardown, _ = cp.cuda.runtime.memGetInfo()
    pool_used_after = int(pool.used_bytes())
    pool_total_after = int(pool.total_bytes())
    pinned_free_blocks_after = int(pinned_pool.n_free_blocks())
    teardown = {
        "pool_before": pool_before,
        "pool_used_bytes_after_free_all_blocks": pool_used_after,
        "pool_total_bytes_after_free_all_blocks": pool_total_after,
        "pinned_pool_free_blocks_after_free_all_blocks": pinned_free_blocks_after,
        "device_free_after_teardown_bytes": int(free_after_teardown),
        "gate_pass": (
            pool_used_after == 0
            and pool_total_after == 0
            and pinned_free_blocks_after == 0
        ),
    }
    report["teardown"] = teardown
    report["gates"]["explicit_cupy_pool_teardown"] = teardown["gate_pass"]
    report["gate_pass"] = all(report["gates"].values())
    report["status"] = (
        "HF3D_P0P2_EXACT_CASE_ZERO_STEP_PROBE_PASS"
        if report["gate_pass"]
        else "HF3D_P0P2_EXACT_CASE_ZERO_STEP_PROBE_FAILED_STOPPED"
    )
    report["elapsed_wall_s_total"] = (time.perf_counter_ns() - started_ns) / 1.0e9
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark", choices=tuple(BENCHMARKS), required=True)
    arguments = parser.parse_args()
    try:
        report = run_probe(arguments.benchmark)
    except Exception as exc:  # noqa: BLE001 - fail-closed worker boundary
        report = {
            "schema_version": 1,
            "status": "HF3D_P0P2_EXACT_CASE_ZERO_STEP_PROBE_FAILED_STOPPED",
            "gate_pass": False,
            "benchmark_id": arguments.benchmark,
            "exception_type": type(exc).__name__,
            "message": str(exc),
            "hf3d_time_steps_advanced": 0,
        }
    print(pretty_json_bytes(report).decode("utf-8"), end="")
    return 0 if report.get("gate_pass") is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
