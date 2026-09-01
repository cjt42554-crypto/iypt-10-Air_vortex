"""Construct one exact P0-P2 V2 case in a fresh process and advance zero steps."""

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

from p0p2v2_contract import (
    CONTRACT_ID,
    CONTRACT_PATH,
    EFFECTIVE_CONFIG_ROOT,
    EXPECTED_BENCHMARKS,
    EXPECTED_GPU_PYTHON_VERSIONS,
    FORMAL_ROOT,
    FORMAL_SOURCE,
    FORMAL_SOURCE_SHA256,
    REGISTERED_TIMES_S,
    SCHEDULE_ID,
    P0PerformanceError,
    assert_runtime_input_separation,
    canonical_sha256,
    effective_config,
    fixed_schedule_manifest,
    load_contract,
    pretty_json_bytes,
    relative,
    source_config_path,
    strict_json,
)

sys.dont_write_bytecode = True
if str(FORMAL_SOURCE) not in sys.path:
    sys.path.insert(0, str(FORMAL_SOURCE))

# The formal package applies its numerical-thread policy before any numerical
# dependency is imported in this fresh probe process.
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


def _array_identity(value: Any) -> dict[str, Any]:
    array = np.asarray(value)
    contiguous = np.ascontiguousarray(array)
    payload = contiguous.tobytes(order="C")
    numeric = contiguous.dtype.kind in "biufc"
    finite = bool(np.isfinite(contiguous).all()) if numeric else True
    result: dict[str, Any] = {
        "shape": list(contiguous.shape),
        "dtype": contiguous.dtype.str,
        "byte_length": len(payload),
        "sha256_c_order_bytes": hashlib.sha256(payload).hexdigest(),
        "all_finite": finite,
    }
    if contiguous.size and numeric:
        result["minimum"] = float(np.min(contiguous))
        result["maximum"] = float(np.max(contiguous))
    return result


def _dataclass_summary(value: Any) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for field in fields(value):
        item = getattr(value, field.name)
        if isinstance(item, np.ndarray):
            summary[field.name] = _array_identity(item)
        elif hasattr(item, "__dataclass_fields__"):
            summary[field.name] = _dataclass_summary(item)
        elif isinstance(item, np.generic):
            summary[field.name] = item.item()
        else:
            summary[field.name] = item
    return summary


def _finite_tree(value: object) -> bool:
    if isinstance(value, bool | int | str | type(None)):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, dict):
        return all(_finite_tree(item) for item in value.values())
    if isinstance(value, list | tuple):
        return all(_finite_tree(item) for item in value)
    return False


def _versions() -> dict[str, str]:
    return {
        "python": platform.python_version(),
        "numpy": np.__version__,
        "scipy": scipy.__version__,
        "cupy": cp.__version__,
        "cupy_distribution": importlib.metadata.version("cupy-cuda13x"),
    }


def run_probe(benchmark: str) -> dict[str, Any]:
    if benchmark not in EXPECTED_BENCHMARKS:
        raise P0PerformanceError(f"unknown P0-P2 V2 benchmark: {benchmark}")
    started_ns = time.perf_counter_ns()
    contract = load_contract()
    schedule = fixed_schedule_manifest()
    computed_payload, computed_identity = effective_config(benchmark)
    effective_path = EFFECTIVE_CONFIG_ROOT / f"P0P2V2_{benchmark}.json"
    generated_payload = strict_json(effective_path)
    if generated_payload != computed_payload:
        raise P0PerformanceError(
            "generated P0-P2 V2 effective config differs from deterministic payload"
        )
    runtime_input_gates = assert_runtime_input_separation(
        [CONTRACT_PATH, FORMAL_SOURCE, source_config_path(benchmark), effective_path]
    )
    contract_binding = contract["p0_p"]["benchmark_definitions"].get(benchmark)
    if not (
        contract.get("contract_id") == CONTRACT_ID
        and contract.get("schedule_id") == SCHEDULE_ID
        and contract["p0_p"].get("fixed_schedule") == schedule
        and isinstance(contract_binding, dict)
        and contract_binding.get("effective_identity") == computed_identity
    ):
        raise P0PerformanceError("zero-step V2 contract/config binding failed")

    source_identity = dict(source_tree_identity(FORMAL_ROOT))
    config = SimulationConfig.from_dict(generated_payload)
    round_trip = config.to_dict()
    backend = HighFidelityBackend()
    pool = cp.get_default_memory_pool()
    pinned_pool = cp.get_default_pinned_memory_pool()
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

    properties = cp.cuda.runtime.getDeviceProperties(0)
    raw_name = properties.get("name", b"")
    device_name = (
        raw_name.decode("utf-8", errors="strict")
        if isinstance(raw_name, bytes)
        else str(raw_name)
    )
    versions = _versions()
    state_arrays = {
        "u_x_face": _array_identity(state.u_x_face),
        "u_y_face": _array_identity(state.u_y_face),
        "u_z_face": _array_identity(state.u_z_face),
        "phase": _array_identity(state.phase),
        "pressure_pa": _array_identity(state.pressure_pa),
    }
    diagnostics_summary = _dataclass_summary(diagnostics)
    thread_policy = NUMERICAL_THREAD_POLICY.to_dict()
    gates = {
        "formal_source_tree_exact": source_identity.get("available") is True
        and source_identity.get("value") == FORMAL_SOURCE_SHA256,
        "formal_config_exact_round_trip": round_trip == generated_payload,
        "effective_config_canonical_hash_exact": canonical_sha256(round_trip)
        == EXPECTED_BENCHMARKS[benchmark]["effective_config_sha256"],
        "runtime_inputs_separated_from_v1_and_e0": all(
            runtime_input_gates.values()
        ),
        "duration_ramp_and_registered_schedule_exact": (
            config.run.duration_s == config.drive.ramp_duration_s == 0.005
            and config.run.output_interval_s == 0.001
            and tuple(REGISTERED_TIMES_S)
            == (0.0, 0.001, 0.002, 0.003, 0.004, 0.005)
            and config.drive.actual_speed_schedule_time_s == ()
        ),
        "actual_cuda_without_fallback": accelerator.get("selected") == "cuda"
        and accelerator.get("fallback") is False
        and config.high_fidelity.pressure_solver_device == "cuda",
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
        "initial_state_zero_steps": state.time_s == 0.0
        and state.step == 0
        and diagnostics.dt_s == 0.0
        and diagnostics.time_step_limiter == "initial_state",
        "state_arrays_finite": all(item["all_finite"] for item in state_arrays.values()),
        "zero_step_diagnostics_finite": _finite_tree(diagnostics_summary),
        "initial_frame_exactly_once": recorder.times == [0.0]
        and len(recorder.phase_frames)
        == len(recorder.ur_frames)
        == len(recorder.uz_frames)
        == len(recorder.utheta_frames)
        == len(recorder.pressure_frames)
        == 1,
        "diagnostic_buffer_empty": all(
            len(value) == 0
            for value in vars(diagnostics_buffer).values()
            if isinstance(value, list)
        ),
        "event_tracker_initialised_without_events": events == [],
        "expected_toolchain_versions": versions == EXPECTED_GPU_PYTHON_VERSIONS,
        "formal_thread_policy_selected_two": thread_policy.get("selected_threads")
        == 2,
        "omp_dynamic_false": os.environ.get("OMP_DYNAMIC") == "FALSE",
        "hf3d_time_steps_advanced_exactly_zero": state.step == 0,
    }
    report: dict[str, Any] = {
        "schema_version": 2,
        "status": "HF3D_P0P2_V2_EXACT_CASE_ZERO_STEP_PROBE_PASS",
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
        "config_identity": computed_identity,
        "formal_source_identity": source_identity,
        "toolchain": {
            "versions": versions,
            "numerical_thread_policy": thread_policy,
            "cuda_runtime_version": int(cp.cuda.runtime.runtimeGetVersion()),
            "cuda_driver_version": int(cp.cuda.runtime.driverGetVersion()),
            "device_name": device_name,
            "compute_capability": (
                f"{int(properties.get('major', 0))}.{int(properties.get('minor', 0))}"
            ),
            "total_global_memory_bytes": int(properties.get("totalGlobalMem", 0)),
        },
        "grid": {"shape": list(grid.shape)},
        "pressure_engine": {
            "class": f"{projection.__class__.__module__}.{projection.__class__.__qualname__}",
            "accelerator": dict(accelerator),
            "cuda_memory": {
                "free_before_projection_bytes": int(free_before),
                "free_after_projection_bytes": int(free_after_projection),
                "total_bytes": int(total_memory),
            },
        },
        "initial_state": {
            "time_s": state.time_s,
            "step": state.step,
            "arrays": state_arrays,
        },
        "zero_step_diagnostics": diagnostics_summary,
        "initial_frame": {"times_s": list(recorder.times)},
        "hf3d_time_steps_advanced": 0,
        "elapsed_wall_s_before_teardown": (time.perf_counter_ns() - started_ns)
        / 1.0e9,
    }

    del tracker, events, diagnostics_buffer, recorder, diagnostics
    del state, projection, grid, backend, config
    gc.collect()
    cp.cuda.runtime.deviceSynchronize()
    pool.free_all_blocks()
    pinned_pool.free_all_blocks()
    cp.cuda.runtime.deviceSynchronize()
    teardown = {
        "pool_used_bytes_after_free_all_blocks": int(pool.used_bytes()),
        "pool_total_bytes_after_free_all_blocks": int(pool.total_bytes()),
        "pinned_pool_free_blocks_after_free_all_blocks": int(
            pinned_pool.n_free_blocks()
        ),
    }
    teardown["gate_pass"] = (
        teardown["pool_used_bytes_after_free_all_blocks"] == 0
        and teardown["pool_total_bytes_after_free_all_blocks"] == 0
        and teardown["pinned_pool_free_blocks_after_free_all_blocks"] == 0
    )
    report["teardown"] = teardown
    report["gates"]["explicit_cupy_pool_teardown"] = teardown["gate_pass"]
    report["gate_pass"] = all(report["gates"].values())
    report["status"] = (
        "HF3D_P0P2_V2_EXACT_CASE_ZERO_STEP_PROBE_PASS"
        if report["gate_pass"]
        else "HF3D_P0P2_V2_EXACT_CASE_ZERO_STEP_PROBE_FAILED_STOPPED"
    )
    report["elapsed_wall_s_total"] = (time.perf_counter_ns() - started_ns) / 1.0e9
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--benchmark", choices=tuple(EXPECTED_BENCHMARKS), required=True
    )
    arguments = parser.parse_args()
    try:
        report = run_probe(arguments.benchmark)
    except Exception as exc:  # noqa: BLE001 - fail-closed subprocess boundary.
        report = {
            "schema_version": 2,
            "status": "HF3D_P0P2_V2_EXACT_CASE_ZERO_STEP_PROBE_FAILED_STOPPED",
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
