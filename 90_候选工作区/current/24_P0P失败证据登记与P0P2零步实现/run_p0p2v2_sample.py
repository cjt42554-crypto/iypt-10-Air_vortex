#!/usr/bin/env python3
"""Run one fresh, unchanged-formal-source HF3D P0-P sample.

The campaign controller launches this file in a new GPU Python process for
every frozen schedule item.  Only ``run.duration_s`` differs from the bound
D3R1 source configuration.  Timed samples carry only the measurements needed
by the frozen contract; separate profiled samples additionally enable NVTX,
CuPy allocation hooks, tracemalloc, cProfile (from the launcher), and Nsight
Systems (from the launcher).
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import gc
import importlib.metadata
import json
import math
import os
import platform
import resource
import sys
import time
import traceback
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from p0p2v2_allocation import load_observer_control
from p0p2v2_consumption import CAMPAIGN_LOCK_PATH, validate_worker_launch
from p0p2v2_contract import (
    EXPECTED_GPU_PYTHON_VERSIONS,
    FORMAL_ROOT,
    FORMAL_SOURCE,
    FORMAL_SOURCE_SHA256,
    GPU_PYTHON,
    RAW_ROOT,
    REGISTERED_TIMES_S,
    REPOSITORY_ROOT,
    P0PerformanceError,
    atomic_json,
    canonical_sha256,
    effective_config,
    execution_binding,
    load_authorization,
    numerical_quality_gates,
    relative,
    sample_identity,
    sha256_file,
    strict_json,
)
from p0p2v2_instrumentation import (
    DeviceAllocationRecorder,
    IntervalRegistry,
    PressureProjectProxy,
    TransparentCallable,
    audit_host_semantic_evidence,
)
from p0p2v2_numerical import archive_numerical_payload
from p0p2v2_telemetry import (
    ancestor_pids,
    evaluate_instantaneous_environment,
    instantaneous_environment,
)


def _early_argument(name: str) -> str:
    try:
        index = sys.argv.index(name)
        value = sys.argv[index + 1]
    except (ValueError, IndexError) as exc:
        raise P0PerformanceError(f"missing early worker argument: {name}") from exc
    return value


def _publish_early_worker_identity() -> dict[str, Any]:
    sample_id = _early_argument("--sample-id")
    mode = _early_argument("--mode")
    output_argument = Path(_early_argument("--output-dir"))
    pid_argument = Path(_early_argument("--pid-file"))
    if output_argument.is_symlink() or pid_argument.is_symlink():
        raise P0PerformanceError("early worker output/PID path must not be a symlink")
    output_dir = output_argument.absolute()
    pid_file = pid_argument.absolute()
    try:
        sequence_index = int(_early_argument("--sequence-index"))
        orchestrator_pid = int(_early_argument("--orchestrator-pid"))
    except ValueError as exc:
        raise P0PerformanceError(
            "early worker numeric launch identity is invalid"
        ) from exc
    launch_token = _early_argument("--launch-token")
    lock_path = Path(_early_argument("--lock-path")).absolute()
    if lock_path != CAMPAIGN_LOCK_PATH.absolute():
        raise P0PerformanceError("early worker lock path differs from frozen path")
    identity = sample_identity(sample_id)
    if identity["mode"] != mode:
        raise P0PerformanceError("early worker mode differs from frozen sample ID")
    if (
        output_dir.parent != RAW_ROOT.resolve()
        or not output_dir.name.startswith(f".{sample_id}.staging.")
        or not output_dir.is_dir()
        or output_dir.is_symlink()
        or pid_file != output_dir / "worker_pid.json"
        or pid_file.exists()
        or pid_file.is_symlink()
    ):
        raise P0PerformanceError("early worker output/PID path validation failed")
    launch_binding = validate_worker_launch(
        sample_id=sample_id,
        sequence_index=sequence_index,
        launch_token=launch_token,
        orchestrator_pid=orchestrator_pid,
        lock_path=lock_path,
        staging_path=output_dir,
    )
    raw_stat = (Path("/proc") / str(os.getpid()) / "stat").read_text(encoding="utf-8")
    process_start_ticks = int(raw_stat.rsplit(")", 1)[1].split()[19])
    record = {
        "pid": os.getpid(),
        "process_start_ticks": process_start_ticks,
        "sample_id": sample_id,
        "mode": mode,
        "published_monotonic_ns": time.monotonic_ns(),
        "launch_binding": launch_binding,
    }
    atomic_json(pid_file, record)
    return record


def _wait_for_monitor_release(
    worker: Mapping[str, Any], *, timeout_s: float = 120.0
) -> dict[str, Any]:
    output_dir = Path(_early_argument("--output-dir")).absolute()
    ready_path = output_dir / "monitors_ready.json"
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if ready_path.is_file() and not ready_path.is_symlink():
            ready = strict_json(ready_path)
            starts = ready.get("monitor_starts")
            if not (
                set(ready)
                == {
                    "schema_version",
                    "status",
                    "target_pid",
                    "target_start_ticks",
                    "published_monotonic_ns",
                    "monitor_starts",
                }
                and ready.get("schema_version") == 2
                and ready.get("status")
                == "HF3D_P0P2_V2_ALL_EXTERNAL_MONITORS_STARTED"
                and ready.get("target_pid") == worker["pid"]
                and ready.get("target_start_ticks") == worker["process_start_ticks"]
                and isinstance(ready.get("published_monotonic_ns"), int)
                and isinstance(starts, list)
                and len(starts) == 5
                and {item.get("kind") for item in starts if isinstance(item, Mapping)}
                == {"gpu", "tctl", "proc", "pidstat", "background"}
                and all(
                    isinstance(item, Mapping)
                    and item.get("record_type") == "monitor_start"
                    and item.get("target_pid") == worker["pid"]
                    and item.get("target_start_ticks") == worker["process_start_ticks"]
                    for item in starts
                )
            ):
                raise P0PerformanceError("external monitor release record is invalid")
            return ready
        orchestrator = int(worker["launch_binding"]["orchestrator_pid"])
        expected_ticks = int(worker["launch_binding"]["orchestrator_start_ticks"])
        try:
            raw = (Path("/proc") / str(orchestrator) / "stat").read_text(
                encoding="utf-8"
            )
            current_ticks = int(raw.rsplit(")", 1)[1].split()[19])
        except (OSError, ValueError, IndexError) as exc:
            raise P0PerformanceError(
                "orchestrator exited before external monitors became ready"
            ) from exc
        if current_ticks != expected_ticks:
            raise P0PerformanceError(
                "orchestrator PID was reused before monitor release"
            )
        time.sleep(0.02)
    raise P0PerformanceError("external monitor release timed out")


# A direct process invocation must fail before importing airvortex, NumPy,
# SciPy, or CuPy while the committed authorization object is a denial.
_EARLY_WORKER_IDENTITY: dict[str, Any] | None = None
_MONITOR_READY_IDENTITY: dict[str, Any] | None = None
_BOOTSTRAP_REGISTRY: IntervalRegistry | None = None
_BOOTSTRAP_CONTEXT: Any | None = None
_WORKER_MEASUREMENT_START_NS: int | None = None


class _EarlyNvtxBackend:
    """Use the NVTX C ABI without importing CuPy before formal thread policy."""

    def __init__(self) -> None:
        library_name = ctypes.util.find_library("nvToolsExt") or "libnvToolsExt.so.1"
        try:
            self._library = ctypes.CDLL(library_name)
        except OSError as exc:
            raise P0PerformanceError("NVTX runtime library is unavailable") from exc
        self._push = self._library.nvtxRangePushA
        self._push.argtypes = [ctypes.c_char_p]
        self._push.restype = ctypes.c_int
        self._pop = self._library.nvtxRangePop
        self._pop.argtypes = []
        self._pop.restype = ctypes.c_int

    def push(self, name: str) -> None:
        if self._push(name.encode("utf-8")) < 0:
            raise P0PerformanceError("NVTX range push failed")

    def pop(self) -> None:
        if self._pop() < 0:
            raise P0PerformanceError("NVTX range pop failed")


if __name__ == "__main__":
    load_authorization(require_dynamic=True)
    _WORKER_MEASUREMENT_START_NS = time.perf_counter_ns()
    early_mode = _early_argument("--mode")
    if early_mode in {"host", "cuda"}:
        early_nvtx = None
        if early_mode == "cuda":
            early_nvtx = _EarlyNvtxBackend()
        _BOOTSTRAP_REGISTRY = IntervalRegistry(nvtx=early_nvtx)
        _BOOTSTRAP_CONTEXT = _BOOTSTRAP_REGISTRY.range("P0P2V2_PROCESS_START")
        _BOOTSTRAP_CONTEXT.__enter__()
    try:
        _EARLY_WORKER_IDENTITY = _publish_early_worker_identity()
        _MONITOR_READY_IDENTITY = _wait_for_monitor_release(_EARLY_WORKER_IDENTITY)
    except BaseException:
        if _BOOTSTRAP_CONTEXT is not None:
            _BOOTSTRAP_CONTEXT.__exit__(*sys.exc_info())
        raise

# Import the formal package first.  Its __init__ applies the frozen early
# numerical-thread policy before it imports NumPy/SciPy-backed public APIs.
if str(FORMAL_SOURCE) not in sys.path:
    sys.path.insert(0, str(FORMAL_SOURCE))

import airvortex.hf3d.stepper as formal_stepper
import cupy as cp
import numpy as np
import scipy
from airvortex._hardware import NUMERICAL_THREAD_POLICY
from airvortex.backends.high_fidelity_3d import (
    CAPABILITIES,
    LIMITATIONS,
    MODEL_NAME,
    HighFidelityBackend,
)
from airvortex.core import Event, RunStatus, SimulationConfig
from airvortex.hf3d.events import TopologyEventTracker
from airvortex.hf3d.pressure import make_pressure_projection
from airvortex.hf3d.recording import (
    FrameRecorder,
    StepDiagnosticsBuffer,
    assemble_result,
)
from airvortex.hf3d.timestep import (
    propose_time_step,
    retry_time_step_s,
)
from airvortex.hf3d.types import (
    HF3DPostProjectionCFLExceeded,
    HF3DState,
    HF3DStepDiagnostics,
)
from airvortex.io import load_run_archive, save_run_archive
from airvortex.io.archive import (
    git_revision_identity,
    source_tree_identity,
)
from airvortex.numerics.cartesian3d import ImmersedCartesianGrid
from numpy.typing import NDArray

PROTOCOL_ID = "HF3D_P0P2_PRODUCTION_EQUIVALENT_BASELINE_AND_PROFILER_V2"
EXPECTED_DEVICE_NAME = "NVIDIA GeForce RTX 5060 Laptop GPU"
EXPECTED_ONLINE_CPUS = 32


@dataclass(slots=True)
class CaseRuntime:
    label: str
    config: SimulationConfig
    config_identity: dict[str, Any]
    grid: ImmersedCartesianGrid
    state: HF3DState
    projection: Any
    pressure_observer: PressureProjectProxy | None
    pressure_accelerator: dict[str, object]
    initial_volume_m3: float
    expected_volume_m3: float
    recorder: FrameRecorder
    diagnostics: StepDiagnosticsBuffer
    events: list[Event]
    event_tracker: TopologyEventTracker
    last_diagnostics: HF3DStepDiagnostics


@contextmanager
def phase_timer(
    phases: dict[str, float],
    name: str,
    *,
    registry: IntervalRegistry | None,
    attributed: bool = True,
) -> Iterator[None]:
    start = time.perf_counter_ns()
    interval = (
        registry.range(name, attributed=attributed)
        if registry is not None
        else nullcontext()
    )
    with interval:
        yield
    elapsed = (time.perf_counter_ns() - start) / 1.0e9
    phases[name] = phases.get(name, 0.0) + elapsed


def install_stepper_wrappers(registry: IntervalRegistry) -> dict[str, Any]:
    boundaries = {
        "advect_bounded_scalar": "phase_advection",
        "conservative_cls_3d": "interface_compression",
        "mixture_property_3d": "mixture_property_construction",
        "advect_momentum_3d": "momentum_advection",
        "newtonian_stress_acceleration_3d": "viscous_acceleration",
        "surface_tension_acceleration_3d": "surface_tension_acceleration",
        "relax_no_slip_boundaries_3d": "boundary_relaxation",
        "rotating_frame_acceleration_3d": "rotating_frame_acceleration",
        "rotating_to_laboratory_velocity_3d": "laboratory_frame_conversion",
        "topology_diagnostics_3d": "topology_diagnostics",
        "_maximum_vector_magnitude": "maximum_vector_diagnostics",
    }
    wrappers: dict[str, Any] = {}
    for attribute, label in boundaries.items():
        original = getattr(formal_stepper, attribute)
        wrapped = TransparentCallable(original, name=label, registry=registry)
        setattr(formal_stepper, attribute, wrapped)
        wrappers[attribute] = wrapped
    return wrappers


def install_runtime_wrappers(
    runtime: CaseRuntime,
    registry: IntervalRegistry,
) -> dict[str, Any]:
    wrappers: dict[str, Any] = install_stepper_wrappers(registry)
    for attribute, label in (
        ("faces_to_cells", "face_to_cell_conversion"),
        ("cells_to_faces", "cell_to_face_conversion"),
    ):
        wrapped = TransparentCallable(
            getattr(runtime.grid, attribute),
            name=label,
            registry=registry,
        )
        setattr(runtime.grid, attribute, wrapped)
        wrappers[f"grid.{attribute}"] = wrapped
    record_frame = TransparentCallable(
        runtime.recorder.record_frame,
        name="frame_recording",
        registry=registry,
    )
    runtime.recorder.record_frame = record_frame  # type: ignore[method-assign]
    wrappers["recorder.record_frame"] = record_frame
    return wrappers


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def all_finite(value: object) -> bool:
    if isinstance(value, np.ndarray):
        return bool(np.isfinite(value).all())
    if isinstance(value, np.generic):
        return bool(np.isfinite(value))
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, (str, int, bool, type(None))):
        return True
    if is_dataclass(value):
        return all(all_finite(getattr(value, field.name)) for field in fields(value))
    if isinstance(value, Mapping):
        return all(all_finite(item) for item in value.values())
    if isinstance(value, Sequence):
        return all(all_finite(item) for item in value)
    return True


def clone_state(state: HF3DState) -> HF3DState:
    return HF3DState(
        time_s=state.time_s,
        step=state.step,
        bar_angle_rad=state.bar_angle_rad,
        u_x_face=state.u_x_face.copy(),
        u_y_face=state.u_y_face.copy(),
        u_z_face=state.u_z_face.copy(),
        phase=state.phase.copy(),
        pressure_pa=state.pressure_pa.copy(),
    )


def runtime_arrays(runtime: CaseRuntime) -> dict[str, NDArray[Any]]:
    arrays: dict[str, NDArray[Any]] = {
        "state_u_x_face": runtime.state.u_x_face,
        "state_u_y_face": runtime.state.u_y_face,
        "state_u_z_face": runtime.state.u_z_face,
        "state_phase": runtime.state.phase,
        "state_pressure_pa": runtime.state.pressure_pa,
        "frame_time_s": np.asarray(runtime.recorder.times, dtype=np.float64),
        "grid_bar_mask": runtime.grid.bar_mask.astype(np.uint8),
        "grid_active_mask": runtime.grid.active_mask.astype(np.uint8),
    }
    for name, value in vars(runtime.diagnostics).items():
        if isinstance(value, list):
            arrays[f"diagnostic_{name}"] = np.asarray(value, dtype=np.float64)
    for name, value in runtime.recorder.observable_lists.items():
        arrays[f"observable_{name}"] = np.asarray(value, dtype=np.float64)
    return arrays


def save_partial(path: Path, runtime: CaseRuntime) -> dict[str, Any]:
    if path.exists():
        raise FileExistsError(path)
    # NumPy's current stub types all keyword payload arrays as the boolean
    # ``allow_pickle`` option, although the runtime API accepts named arrays.
    np.savez_compressed(path, **runtime_arrays(runtime))  # type: ignore[arg-type]
    return {
        "path": relative(path),
        "sha256": sha256_file(path),
        "size_bytes": path.stat().st_size,
    }


def _cpu_model() -> str:
    for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
        if line.startswith("model name") and ":" in line:
            return line.split(":", 1)[1].strip()
    raise P0PerformanceError("worker CPU model is unavailable")


def thread_and_runtime_identity(
    *,
    expected_claim_gpu_identity: Mapping[str, Any],
    expected_claim_toolchain: Mapping[str, Any],
) -> dict[str, Any]:
    properties = cp.cuda.runtime.getDeviceProperties(0)
    raw_name = properties.get("name", b"unknown")
    device_name = (
        raw_name.decode("utf-8", errors="replace")
        if isinstance(raw_name, bytes)
        else str(raw_name)
    )
    versions = {
        "python": platform.python_version(),
        "numpy": np.__version__,
        "scipy": scipy.__version__,
        "cupy": cp.__version__,
        "cupy_distribution": importlib.metadata.version("cupy-cuda13x"),
    }
    policy = NUMERICAL_THREAD_POLICY.to_dict()
    applied = dict(cast(Sequence[tuple[str, str]], policy.get("applied", [])))
    expected_applied = {
        "OPENBLAS_NUM_THREADS": "2",
        "OMP_NUM_THREADS": "2",
        "MKL_NUM_THREADS": "2",
        "BLIS_NUM_THREADS": "2",
        "VECLIB_MAXIMUM_THREADS": "2",
        "NUMEXPR_NUM_THREADS": "2",
    }
    environment_snapshot = instantaneous_environment()
    environment_gate = evaluate_instantaneous_environment(
        environment_snapshot, excluded_pids=ancestor_pids()
    )
    nvidia_gpu = environment_snapshot["gpu"]["gpu"]
    claim_gpu = expected_claim_gpu_identity.get("gpu")
    bound_toolchain = expected_claim_toolchain.get("gpu_python_and_cuda")
    bound_nvidia = expected_claim_toolchain.get("nvidia_smi_gpu_identity")
    bound_host = expected_claim_toolchain.get("host_policy")
    gates = {
        "versions": versions == EXPECTED_GPU_PYTHON_VERSIONS,
        "device_name": device_name == EXPECTED_DEVICE_NAME,
        "cuda_fp64_probe": float(cp.asnumpy(cp.asarray([1.0]) * 2.0)[0]) == 2.0,
        "thread_requested_auto": policy.get("requested") == "auto",
        "thread_selected_two": policy.get("selected_threads") == 2,
        "thread_not_explicit_override": policy.get(
            "explicitly_overrode_library_environment"
        )
        is False,
        "thread_applied_exact": applied == expected_applied,
        "thread_preserved_empty": policy.get("preserved") in ((), []),
        "omp_dynamic_false": os.environ.get("OMP_DYNAMIC") == "FALSE",
        "affinity_full": len(os.sched_getaffinity(0)) == EXPECTED_ONLINE_CPUS,
        "gpu_python_exact": Path(sys.executable).resolve() == GPU_PYTHON.resolve(),
        "instantaneous_worker_environment": environment_gate["gate_pass"],
        "claim_gpu_identity_mapping": isinstance(claim_gpu, Mapping),
        "claim_gpu_name_uuid_driver_exact": isinstance(claim_gpu, Mapping)
        and all(
            nvidia_gpu.get(field) == claim_gpu.get(field)
            for field in ("name", "uuid", "driver_version", "memory.total")
        ),
        "ticket_gpu_python_toolchain_exact": isinstance(bound_toolchain, Mapping)
        and bound_toolchain.get("gpu_python_realpath")
        == str(Path(sys.executable).resolve())
        and bound_toolchain.get("python_version") == versions["python"]
        and bound_toolchain.get("cupy_version") == versions["cupy"]
        and bound_toolchain.get("cuda_driver_version")
        == int(cp.cuda.runtime.driverGetVersion())
        and bound_toolchain.get("cuda_runtime_version")
        == int(cp.cuda.runtime.runtimeGetVersion())
        and bound_toolchain.get("cuda_device_name") == device_name
        and bound_toolchain.get("numerical_thread_policy") == policy,
        "ticket_nvidia_identity_exact": isinstance(bound_nvidia, Mapping)
        and all(
            nvidia_gpu.get(field) == bound_nvidia.get(field)
            for field in ("name", "uuid", "driver_version")
        ),
        "ticket_host_identity_exact": isinstance(bound_host, Mapping)
        and bound_host.get("expected_online_logical_cpus") == EXPECTED_ONLINE_CPUS
        and bound_host.get("observed_affinity") == sorted(os.sched_getaffinity(0))
        and bound_host.get("cpu_model") == _cpu_model()
        and bound_host.get("governors") == environment_snapshot["governors"]
        and bound_host.get("ac_online") == environment_snapshot["ac_online"]
        and bound_host.get("formal_selected_blas_threads") == 2
        and bound_host.get("omp_dynamic") == "FALSE",
    }
    failed = [name for name, passed in gates.items() if not passed]
    if failed:
        raise P0PerformanceError("sample runtime identity failed: " + ", ".join(failed))
    return {
        "gate_pass": True,
        "gates": gates,
        "versions": versions,
        "python_executable": sys.executable,
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "cuda_runtime_version": int(cp.cuda.runtime.runtimeGetVersion()),
        "cuda_driver_version": int(cp.cuda.runtime.driverGetVersion()),
        "device_name": device_name,
        "compute_capability": (
            f"{int(properties.get('major', 0))}.{int(properties.get('minor', 0))}"
        ),
        "thread_policy": policy,
        "claim_gpu_identity": dict(expected_claim_gpu_identity),
        "claim_toolchain_binding": dict(expected_claim_toolchain),
        "instantaneous_environment": environment_gate,
        "cpu_model": _cpu_model(),
        "environment": {
            key: os.environ.get(key)
            for key in (
                "AIRVORTEX_BLAS_THREADS",
                "OPENBLAS_NUM_THREADS",
                "OMP_NUM_THREADS",
                "MKL_NUM_THREADS",
                "BLIS_NUM_THREADS",
                "VECLIB_MAXIMUM_THREADS",
                "NUMEXPR_NUM_THREADS",
                "OMP_DYNAMIC",
                "CUDA_VISIBLE_DEVICES",
            )
        },
        "affinity": sorted(os.sched_getaffinity(0)),
    }


def prepare_runtime(
    benchmark: str,
    *,
    registry: IntervalRegistry | None,
) -> CaseRuntime:
    payload, config_identity = effective_config(benchmark)
    config = SimulationConfig.from_dict(payload)
    backend = HighFidelityBackend()
    grid = backend.make_grid(config)
    projection, accelerator = make_pressure_projection(config, grid)
    if not (
        accelerator.get("selected") == "cuda" and accelerator.get("fallback") is False
    ):
        raise P0PerformanceError(f"{benchmark} did not select actual CUDA")
    observer: PressureProjectProxy | None = None
    effective_projection: Any = projection
    if registry is not None:
        cuda_projection = cast(Any, projection)
        project_device = TransparentCallable(
            cuda_projection.project_device,
            name="projection.project_device",
            registry=registry,
        )
        cuda_projection.project_device = project_device
        observer = PressureProjectProxy(cuda_projection, registry)
        effective_projection = observer
    state = backend.initialise_state(config, grid)
    initial_volume = backend._initial_liquid_volume(config, grid)
    recorder = FrameRecorder(config, grid, state, initial_volume)
    diagnostics = StepDiagnosticsBuffer(initial_volume)
    events: list[Event] = []
    tracker = TopologyEventTracker(config, events)
    zero = backend._zero_step_diagnostics(state, config, grid, initial_volume)
    recorder.record_frame(zero)
    tracker.initialise(zero.topology)
    return CaseRuntime(
        label=benchmark,
        config=config,
        config_identity=config_identity,
        grid=grid,
        state=state,
        projection=effective_projection,
        pressure_observer=observer,
        pressure_accelerator=dict(accelerator),
        initial_volume_m3=initial_volume,
        expected_volume_m3=initial_volume,
        recorder=recorder,
        diagnostics=diagnostics,
        events=events,
        event_tracker=tracker,
        last_diagnostics=zero,
    )


def step_hard_gate(
    runtime: CaseRuntime,
    diagnostics: HF3DStepDiagnostics,
    quality: Mapping[str, Any],
) -> tuple[bool, list[str]]:
    failed: list[str] = []
    if not all_finite(runtime.state) or not all_finite(diagnostics):
        failed.append("all_state_and_diagnostic_values_finite")
    if diagnostics.pressure_relative_residual > float(
        quality["pressure_relative_residual_max"]
    ):
        failed.append("pressure_relative_residual")
    if diagnostics.divergence_after_s_inv > float(
        quality["projected_divergence_max_s_inv"]
    ):
        failed.append("projected_divergence")
    if diagnostics.advective_cfl > float(quality["accepted_advective_cfl_max"]) * (
        1.0 + 1.0e-12
    ):
        failed.append("accepted_advective_cfl")
    balance = (
        abs(diagnostics.liquid_volume_balance_error_m3) / runtime.initial_volume_m3
    )
    if balance > float(quality["liquid_volume_balance_error_relative_max"]):
        failed.append("liquid_volume_balance_error_relative")
    if diagnostics.cfl_retry_count > int(quality["maximum_cfl_step_retries"]):
        failed.append("maximum_cfl_step_retries")
    if (
        runtime.diagnostics.cumulative_clip / runtime.initial_volume_m3
        > float(quality["cumulative_phase_clip_relative_max"])
    ):
        failed.append("cumulative_phase_clip_relative")
    if (
        runtime.diagnostics.cumulative_repair / runtime.initial_volume_m3
        > float(quality["cumulative_phase_repair_relative_max"])
    ):
        failed.append("cumulative_phase_repair_relative")
    if not (
        runtime.pressure_accelerator.get("selected") == "cuda"
        and runtime.pressure_accelerator.get("fallback") is False
    ):
        failed.append("actual_cuda_without_fallback")
    return not failed, failed


def advance_runtime(
    runtime: CaseRuntime,
    *,
    registry: IntervalRegistry | None,
) -> dict[str, Any]:
    backend = HighFidelityBackend()
    advance_one_step = (
        TransparentCallable(
            backend.advance_one_step,
            name="formal_advance_one_step",
            registry=registry,
        )
        if registry is not None
        else backend.advance_one_step
    )
    registered_times = np.asarray(REGISTERED_TIMES_S, dtype=np.float64)
    end_time = runtime.config.run.duration_s
    if not (
        end_time == REGISTERED_TIMES_S[-1]
        and runtime.config.run.output_interval_s == 0.001
        and np.array_equal(
            registered_times,
            np.arange(registered_times.size, dtype=np.float64) * 0.001,
        )
    ):
        raise P0PerformanceError("short registered output schedule drifted")
    quality = numerical_quality_gates()
    tolerance = 128.0 * np.finfo(float).eps * max(end_time, 1.0)
    start_wall = time.perf_counter_ns()
    start_cpu = time.process_time_ns()
    total_retries = 0
    trial_sequence: list[dict[str, Any]] = []
    next_index = 1
    while next_index < registered_times.size:
        next_target = float(registered_times[next_index])
        while runtime.state.time_s < next_target - tolerance:
            proposal = propose_time_step(runtime.state, runtime.config, runtime.grid)
            remaining = next_target - runtime.state.time_s
            dt_s = min(proposal.stable_dt_s, remaining)
            if not math.isfinite(dt_s) or dt_s <= 0.0:
                raise FloatingPointError("P0-P trial time step became non-positive")
            limiter = (
                "output_alignment"
                if remaining < proposal.stable_dt_s
                else str(proposal.selected_limiter)
            )
            initial_attempt = dt_s
            retry_count = 0
            maximum_rejected = 0.0
            while True:
                checkpoint = runtime.projection.checkpoint_history()
                trial_state = clone_state(runtime.state)
                trial_identity = {
                    "trial_index": len(trial_sequence),
                    "accepted_step_candidate": runtime.state.step + 1,
                    "registered_output_index": next_index,
                    "time_before_s": runtime.state.time_s,
                    "target_time_s": next_target,
                    "dt_s": dt_s,
                    "initial_attempt_dt_s": initial_attempt,
                    "limiter": limiter,
                    "retry_count": retry_count,
                    "proposed_stable_dt_s": float(proposal.stable_dt_s),
                    "proposed_limiter": str(proposal.selected_limiter),
                }
                try:
                    trial_diagnostics = advance_one_step(
                        trial_state,
                        runtime.config,
                        runtime.grid,
                        runtime.projection,
                        dt_s,
                        expected_liquid_volume_m3=runtime.expected_volume_m3,
                        time_step_limiter=limiter,
                        cfl_retry_count=retry_count,
                        initial_attempt_dt_s=initial_attempt,
                        maximum_rejected_post_projection_cfl=maximum_rejected,
                    )
                except HF3DPostProjectionCFLExceeded as exc:
                    runtime.projection.restore_history(checkpoint)
                    rejected_cfl = float(
                        exc.diagnostics.post_projection_advective_cfl
                    )
                    maximum_rejected = max(
                        maximum_rejected,
                        rejected_cfl,
                    )
                    maximum_retries = (
                        runtime.config.high_fidelity.maximum_cfl_step_retries
                    )
                    if retry_count >= maximum_retries:
                        raise FloatingPointError(
                            "P0-P CFL retry gate exhausted at accepted step "
                            f"{runtime.state.step + 1}"
                        ) from exc
                    next_dt_s = retry_time_step_s(
                        dt_s,
                        rejected_cfl,
                        runtime.config.high_fidelity.cfl_limit,
                        runtime.config.high_fidelity.cfl_controller_target_fraction,
                    )
                    trial_sequence.append(
                        {
                            **trial_identity,
                            "status": "REJECTED_POST_PROJECTION_CFL",
                            "post_projection_advective_cfl": rejected_cfl,
                            "next_dt_s": next_dt_s,
                        }
                    )
                    dt_s = next_dt_s
                    retry_count += 1
                    total_retries += 1
                    limiter = "post_projection_cfl_retry"
                    runtime.events.append(
                        Event(
                            runtime.state.time_s,
                            "p0_cfl_step_retry",
                            "Rejected P0-P trial rolled back before a stricter retry.",
                            {
                                "benchmark": runtime.label,
                                "retry_number": retry_count,
                                "next_dt_s": dt_s,
                            },
                        )
                    )
                    continue
                except Exception:
                    runtime.projection.restore_history(checkpoint)
                    raise
                runtime.state = trial_state
                runtime.recorder.state = runtime.state
                runtime.last_diagnostics = trial_diagnostics
                runtime.expected_volume_m3 = trial_diagnostics.expected_liquid_volume_m3
                runtime.event_tracker.update(
                    runtime.state.time_s,
                    trial_diagnostics,
                )
                runtime.diagnostics.append(
                    runtime.state.time_s,
                    trial_diagnostics,
                    runtime.config,
                )
                gate_interval = (
                    registry.range("post_projection_hard_gates")
                    if registry is not None
                    else nullcontext()
                )
                with gate_interval:
                    passed, failed = step_hard_gate(runtime, trial_diagnostics, quality)
                if not passed:
                    raise FloatingPointError(
                        f"{runtime.label} P0-P hard gate failed: {', '.join(failed)}"
                    )
                trial_sequence.append(
                    {
                        **trial_identity,
                        "status": "ACCEPTED",
                        "accepted_time_s": runtime.state.time_s,
                        "pressure_relative_residual": float(
                            trial_diagnostics.pressure_relative_residual
                        ),
                        "pressure_iterations": int(
                            trial_diagnostics.pressure_iterations
                        ),
                        "projected_divergence_s_inv": float(
                            trial_diagnostics.divergence_after_s_inv
                        ),
                        "advective_cfl": float(trial_diagnostics.advective_cfl),
                        "liquid_volume_balance_error_m3": float(
                            trial_diagnostics.liquid_volume_balance_error_m3
                        ),
                        "cumulative_phase_clip_m3": float(
                            runtime.diagnostics.cumulative_clip
                        ),
                        "cumulative_phase_repair_m3": float(
                            runtime.diagnostics.cumulative_repair
                        ),
                    }
                )
                break
        if abs(runtime.state.time_s - next_target) > tolerance:
            raise FloatingPointError(
                f"P0-P skipped registered output target {next_index}"
            )
        runtime.state.time_s = next_target
        runtime.recorder.record_frame(runtime.last_diagnostics)
        print(
            json.dumps(
                {
                    "stage": "p0_sample_progress",
                    "benchmark": runtime.label,
                    "registered_output_index": next_index,
                    "time_s": runtime.state.time_s,
                    "accepted_steps": runtime.state.step,
                    "retry_attempts": total_retries,
                },
                sort_keys=True,
            ),
            flush=True,
        )
        next_index += 1
    return {
        "completed": next_index == registered_times.size
        and math.isclose(
            runtime.state.time_s,
            end_time,
            rel_tol=0.0,
            abs_tol=tolerance,
        ),
        "accepted_steps": runtime.state.step,
        "rejected_trials": total_retries,
        "retry_attempts": total_retries,
        "trial_sequence": trial_sequence,
        "registered_outputs_recorded": next_index,
        "stepping_wall_time_s": (time.perf_counter_ns() - start_wall) / 1.0e9,
        "stepping_process_cpu_time_s": (time.process_time_ns() - start_cpu) / 1.0e9,
    }


def runtime_quality(runtime: CaseRuntime, *, completed: bool) -> dict[str, Any]:
    quality = numerical_quality_gates()
    dt = np.asarray(runtime.diagnostics.step_dt, dtype=np.float64)
    pressure = np.asarray(runtime.diagnostics.step_pressure_residual, dtype=np.float64)
    iterations = np.asarray(
        runtime.diagnostics.step_pressure_iterations, dtype=np.float64
    )
    divergence = np.asarray(runtime.diagnostics.step_divergence, dtype=np.float64)
    cfl = np.asarray(runtime.diagnostics.step_post_projection_cfl, dtype=np.float64)
    retries = np.asarray(runtime.diagnostics.step_cfl_retry_count, dtype=np.float64)
    balance = np.asarray(
        runtime.recorder.observable_lists.get(
            "liquid_volume_balance_error_relative", []
        ),
        dtype=np.float64,
    )
    tolerance = max(1.0e-15, runtime.config.high_fidelity.time_step_s * 1.0e-9)
    active_fraction = (
        float(
            np.mean(np.abs(dt - runtime.config.high_fidelity.time_step_s) <= tolerance)
        )
        if dt.size
        else 0.0
    )
    clip = runtime.diagnostics.cumulative_clip / runtime.initial_volume_m3
    repair = runtime.diagnostics.cumulative_repair / runtime.initial_volume_m3
    frame_time = np.asarray(runtime.recorder.times, dtype=np.float64)
    gates = {
        "complete_to_registered_end_time": completed
        and math.isclose(
            runtime.state.time_s,
            runtime.config.run.duration_s,
            rel_tol=0.0,
            abs_tol=2.0e-15,
        ),
        "all_state_and_diagnostic_values_finite": all_finite(runtime.state)
        and all(
            np.isfinite(value).all()
            for value in (
                dt,
                pressure,
                iterations,
                divergence,
                cfl,
                retries,
                balance,
            )
        ),
        "pressure_relative_residual": bool(pressure.size)
        and float(np.max(pressure)) <= float(quality["pressure_relative_residual_max"]),
        "projected_divergence": bool(divergence.size)
        and float(np.max(divergence))
        <= float(quality["projected_divergence_max_s_inv"]),
        "accepted_advective_cfl": bool(cfl.size)
        and float(np.max(cfl))
        <= float(quality["accepted_advective_cfl_max"]) * (1.0 + 1.0e-12),
        "cumulative_phase_clip_relative": clip
        <= float(quality["cumulative_phase_clip_relative_max"]),
        "cumulative_phase_repair_relative": repair
        <= float(quality["cumulative_phase_repair_relative_max"]),
        "liquid_volume_balance_error_relative": bool(balance.size)
        and float(np.max(np.abs(balance)))
        <= float(quality["liquid_volume_balance_error_relative_max"]),
        "configured_time_step_active_fraction": active_fraction
        >= float(quality["configured_time_step_active_fraction_min"]),
        "maximum_cfl_step_retries": not retries.size
        or float(np.max(retries)) <= float(quality["maximum_cfl_step_retries"]),
        "actual_cuda_without_fallback": runtime.pressure_accelerator.get("selected")
        == "cuda"
        and runtime.pressure_accelerator.get("fallback") is False,
        "native_output_times_exact": bool(
            np.array_equal(
                frame_time,
                np.asarray(REGISTERED_TIMES_S, dtype=np.float64),
            )
        ),
    }
    metrics = {
        "accepted_steps": runtime.state.step,
        "simulated_time_s": runtime.state.time_s,
        "configured_time_step_active_fraction": active_fraction,
        "maximum_pressure_relative_residual": float(np.max(pressure))
        if pressure.size
        else None,
        "pressure_iterations_minimum": int(np.min(iterations))
        if iterations.size
        else None,
        "pressure_iterations_maximum": int(np.max(iterations))
        if iterations.size
        else None,
        "pressure_iterations_mean": float(np.mean(iterations))
        if iterations.size
        else None,
        "maximum_projected_divergence_s_inv": float(np.max(divergence))
        if divergence.size
        else None,
        "maximum_advective_cfl": float(np.max(cfl)) if cfl.size else None,
        "maximum_cfl_retry_count": int(np.max(retries)) if retries.size else None,
        "cumulative_phase_clip_relative": clip,
        "cumulative_phase_repair_relative": repair,
        "maximum_liquid_volume_balance_error_relative": float(np.max(np.abs(balance)))
        if balance.size
        else None,
    }
    return {"gate_pass": all(gates.values()), "gates": gates, "metrics": metrics}


def archive_payloads(path: Path) -> dict[str, Any]:
    return {
        child.name: {
            "sha256": sha256_file(child),
            "size_bytes": child.stat().st_size,
        }
        for child in sorted(path.iterdir())
        if child.is_file()
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample-id", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--mode",
        choices=("unprofiled", "host", "cuda", "memory"),
        required=True,
    )
    parser.add_argument("--pid-file", type=Path, required=True)
    parser.add_argument("--sequence-index", type=int, required=True)
    parser.add_argument("--launch-token", required=True)
    parser.add_argument("--orchestrator-pid", type=int, required=True)
    parser.add_argument("--lock-path", type=Path, required=True)
    return parser.parse_args(argv)


def validate_output_dir(path: Path, sample_id: str) -> Path:
    resolved = path.resolve()
    expected_prefix = f".{sample_id}.staging."
    if resolved.parent != RAW_ROOT.resolve() or not resolved.name.startswith(
        expected_prefix
    ):
        raise P0PerformanceError("sample output directory is outside the frozen root")
    if not resolved.is_dir() or resolved.is_symlink():
        raise P0PerformanceError("sample output directory is unavailable")
    prohibited = (
        "sample_result.json",
        "sample_failure.json",
        "effective_config.json",
        "partial.npz",
        "archive",
    )
    if any((resolved / name).exists() for name in prohibited):
        raise P0PerformanceError("sample output already contains result-bearing data")
    return resolved


def validate_instrumentation_environment(mode: str) -> dict[str, Any]:
    cprofile = os.environ.get("P0P2V2_CPROFILE_ACTIVE") == "1"
    nsys = os.environ.get("P0P2V2_NSYS_ACTIVE") == "1"
    preload = os.environ.get("LD_PRELOAD", "")
    allocator_marker = os.environ.get("P0P2V2_ALLOC_OBSERVER_ACTIVE") == "1"
    allocator_symbol_available = False
    if allocator_marker:
        try:
            load_observer_control()
        except (AttributeError, OSError):
            allocator_symbol_available = False
        else:
            allocator_symbol_available = True
    allocator = allocator_marker and allocator_symbol_available
    expected = {
        "unprofiled": (False, False, False),
        "host": (True, False, False),
        "cuda": (False, True, False),
        "memory": (False, False, True),
    }[mode]
    observed = (cprofile, nsys, allocator)
    if observed != expected:
        raise P0PerformanceError(
            f"{mode} instrumentation separation failed: {observed!r} != {expected!r}"
        )
    if mode != "memory" and preload:
        raise P0PerformanceError("LD_PRELOAD is prohibited outside memory profiles")
    if mode == "memory" and not preload.startswith("/proc/self/fd/"):
        raise P0PerformanceError("memory observer preload is not descriptor-bound")
    return {
        "mode": mode,
        "cprofile_active": cprofile,
        "nsys_active": nsys,
        "native_allocator_observer_active": allocator,
        "native_allocator_symbol_available": allocator_symbol_available,
        "scored_profiler_free": mode != "unprofiled" or not any(observed),
    }


_ACTIVE_RUNTIME: CaseRuntime | None = None


def run_sample(args: argparse.Namespace) -> dict[str, Any]:
    global _ACTIVE_RUNTIME, _BOOTSTRAP_CONTEXT

    load_authorization(require_dynamic=True)
    identity = sample_identity(str(args.sample_id))
    if str(args.mode) != identity["mode"]:
        raise P0PerformanceError("instrumentation mode differs from frozen sample ID")
    instrumentation = validate_instrumentation_environment(identity["mode"])
    output_dir = validate_output_dir(args.output_dir, identity["sample_id"])
    pid_file = args.pid_file.resolve()
    if pid_file.parent != output_dir or pid_file.name != "worker_pid.json":
        raise P0PerformanceError("worker PID file escaped the sample staging directory")
    early_identity = strict_json(pid_file)
    if not (
        early_identity == _EARLY_WORKER_IDENTITY
        and isinstance(_MONITOR_READY_IDENTITY, Mapping)
        and early_identity.get("pid") == os.getpid()
        and early_identity.get("sample_id") == identity["sample_id"]
        and early_identity.get("mode") == identity["mode"]
        and early_identity.get("launch_binding", {}).get("sequence_index")
        == args.sequence_index
        and early_identity.get("launch_binding", {}).get("orchestrator_pid")
        == args.orchestrator_pid
    ):
        raise P0PerformanceError("early worker PID identity changed after imports")
    script_path = Path(__file__).resolve()
    binding = execution_binding(
        [
            script_path,
            script_path.parent / "p0p2v2_contract.py",
            script_path.parent / "p0p2v2_instrumentation.py",
            script_path.parent / "p0p2v2_numerical.py",
        ]
    )
    started_at_utc = utc_now()
    child_start_ns = _WORKER_MEASUREMENT_START_NS or time.perf_counter_ns()
    child_cpu_start_ns = time.process_time_ns()
    phases: dict[str, float] = {}
    semantic_mode = identity["mode"] in {"host", "cuda"}
    registry = _BOOTSTRAP_REGISTRY if semantic_mode else None
    if semantic_mode and registry is None:
        raise P0PerformanceError("semantic bootstrap registry is unavailable")
    if semantic_mode and _BOOTSTRAP_CONTEXT is None:
        raise P0PerformanceError("semantic process range is unavailable")
    device_recorder = (
        DeviceAllocationRecorder(cp) if identity["mode"] == "memory" else None
    )
    memory_context = device_recorder.hook if device_recorder else nullcontext()
    runtime_identity: dict[str, Any]
    source_start: dict[str, Any]
    revision_start: dict[str, Any]
    wrappers: dict[str, Any] = {}
    process_interval = nullcontext()
    device_allocation_interval = (
        registry.range("P0P2V2_DEVICE_ALLOCATION_SCOPE")
        if identity["mode"] == "cuda" and registry is not None
        else nullcontext()
    )
    with process_interval:
        with phase_timer(
            phases,
            "preflight_and_initial_sync",
            registry=registry,
        ):
            launch_binding = early_identity["launch_binding"]
            runtime_identity = thread_and_runtime_identity(
                expected_claim_gpu_identity=launch_binding["claim_gpu_identity"],
                expected_claim_toolchain=launch_binding["claim_toolchain_binding"],
            )
            cp.cuda.runtime.deviceSynchronize()
            source_start = dict(source_tree_identity(FORMAL_ROOT))
            revision_start = dict(git_revision_identity(REPOSITORY_ROOT))
            if source_start.get("value") != FORMAL_SOURCE_SHA256:
                raise P0PerformanceError("formal source drifted at sample start")
            if revision_start.get("value") != binding["execution_commit"]:
                raise P0PerformanceError("Git revision identity failed at sample start")
            effective_payload, config_identity = effective_config(identity["benchmark"])
            atomic_json(output_dir / "effective_config.json", effective_payload)
        with device_allocation_interval, memory_context:
            if device_recorder is not None:
                device_recorder.observe("before_runtime_setup")
            with phase_timer(phases, "runtime_setup", registry=registry):
                runtime = prepare_runtime(
                    identity["benchmark"],
                    registry=registry,
                )
                _ACTIVE_RUNTIME = runtime
                if runtime.config_identity != config_identity:
                    raise P0PerformanceError("effective configuration identity changed")
                if registry is not None:
                    wrappers = install_runtime_wrappers(runtime, registry)
            if device_recorder is not None:
                device_recorder.observe("after_runtime_setup")
            with phase_timer(phases, "solver_phase", registry=registry):
                outcome = advance_runtime(runtime, registry=registry)
            if device_recorder is not None:
                device_recorder.observe("after_solver")
            with phase_timer(phases, "completed_quality_gates", registry=registry):
                quality = runtime_quality(runtime, completed=bool(outcome["completed"]))
                if not quality["gate_pass"]:
                    failed = [
                        name for name, passed in quality["gates"].items() if not passed
                    ]
                    raise P0PerformanceError(
                        "completed sample failed frozen quality gates: "
                        + ", ".join(failed)
                    )
            tolerance = (
                128.0 * np.finfo(float).eps * max(runtime.config.run.duration_s, 1.0)
            )
            with phase_timer(phases, "result_assembly", registry=registry):
                result = assemble_result(
                    runtime.config,
                    runtime.state,
                    runtime.grid,
                    runtime.recorder,
                    runtime.diagnostics,
                    runtime.events,
                    runtime.projection,
                    runtime.pressure_accelerator,
                    RunStatus.COMPLETED,
                    runtime.expected_volume_m3,
                    tolerance,
                    backend_id=f"hf3d_p0_baseline_{identity['benchmark'].lower()}",
                    model_name=MODEL_NAME,
                    capabilities=CAPABILITIES,
                    limitations=LIMITATIONS,
                    numerical_thread_policy=NUMERICAL_THREAD_POLICY.to_dict(),
                )
            source_end = dict(source_tree_identity(FORMAL_ROOT))
            revision_end = dict(git_revision_identity(REPOSITORY_ROOT))
            if not (
                source_start.get("value")
                == source_end.get("value")
                == FORMAL_SOURCE_SHA256
                and revision_start.get("value")
                == revision_end.get("value")
                == binding["execution_commit"]
            ):
                raise P0PerformanceError("source or Git identity drifted during sample")
            archive_path = output_dir / "archive"
            with phase_timer(phases, "archive_write", registry=registry):
                save_run_archive(
                    archive_path,
                    runtime.config,
                    result,
                    extra_provenance={
                        "candidate_protocol_id": PROTOCOL_ID,
                        "sample_id": identity["sample_id"],
                        "benchmark": identity["benchmark"],
                        "disposition": identity["disposition"],
                        "instrumentation_mode": identity["mode"],
                        "fresh_from_zero": True,
                        "checkpoint_used": False,
                        "prior_trajectory_used": False,
                        "formal_g0_geometry": True,
                    },
                    process_cpu_seconds=(time.process_time_ns() - child_cpu_start_ns)
                    / 1.0e9,
                    invocation_entry_point="run_p0p2v2_sample.py",
                    invocation_argument_summary={
                        **identity,
                        "config_canonical_sha256": canonical_sha256(
                            runtime.config.to_dict()
                        ),
                    },
                    source_tree_execution_start=source_start,
                    source_tree_execution_end=source_end,
                    git_revision_execution_start=revision_start,
                    git_revision_execution_end=revision_end,
                )
            with phase_timer(phases, "strict_archive_reload", registry=registry):
                reloaded = load_run_archive(archive_path, verify=True)
                if not (
                    reloaded.result.status is RunStatus.COMPLETED
                    and reloaded.config.to_dict() == runtime.config.to_dict()
                    and np.array_equal(
                        reloaded.result.time_s,
                        np.asarray(REGISTERED_TIMES_S, dtype=np.float64),
                    )
                ):
                    raise P0PerformanceError("strict archive reload identity failed")
                logical_archive_path = RAW_ROOT / identity["sample_id"] / "archive"
                numerical_payload = archive_numerical_payload(
                    archive_path,
                    execution_sequence=outcome["trial_sequence"],
                    logical_archive_path=logical_archive_path,
                )
            with phase_timer(phases, "P0P2V2_FINAL_SYNC_END", registry=registry):
                cp.cuda.runtime.deviceSynchronize()
            if device_recorder is not None:
                device_recorder.observe("after_final_sync")
    if _BOOTSTRAP_CONTEXT is not None:
        _BOOTSTRAP_CONTEXT.__exit__(None, None, None)
        _BOOTSTRAP_CONTEXT = None
    if identity["mode"] == "memory":
        _, stop_observer = load_observer_control()
        stop_observer()
    pool = cp.get_default_memory_pool()
    host_memory: dict[str, Any] = {
        "peak_rss_bytes": int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        * 1024,
        "tracemalloc_enabled": False,
        "native_allocation_observer_expected": identity["mode"] == "memory",
    }
    semantic_records = registry.records() if registry is not None else ()
    pressure_records = (
        runtime.pressure_observer.records
        if runtime.pressure_observer is not None
        else []
    )
    pressure_ranges = [
        record for record in semantic_records if record.name == "pressure.project"
    ]
    child_end_ns = time.perf_counter_ns()
    completed_monotonic_ns = time.monotonic_ns()
    semantic_coverage = (
        audit_host_semantic_evidence(
            cast(IntervalRegistry, registry).to_manifest(),
            child_wall_ns=child_end_ns - child_start_ns,
        )
        if identity["mode"] == "host"
        else None
    )
    report = {
        "schema_version": 2,
        "status": "HF3D_P0P2_V2_SAMPLE_COMPLETE_ALL_HARD_GATES_PASS",
        "gate_pass": True,
        **identity,
        "protocol_id": PROTOCOL_ID,
        "started_at_utc": started_at_utc,
        "completed_at_utc": utc_now(),
        "completed_monotonic_ns": completed_monotonic_ns,
        "execution_binding": binding,
        "runtime_identity": runtime_identity,
        "external_monitor_release": _MONITOR_READY_IDENTITY,
        "config_identity": runtime.config_identity,
        "source_tree_execution_start": source_start,
        "source_tree_execution_end": source_end,
        "git_revision_execution_start": revision_start,
        "git_revision_execution_end": revision_end,
        "fresh_evidence": {
            "started_from_zero": True,
            "checkpoint_used": False,
            "prior_trajectory_used": False,
            "fresh_process": True,
        },
        "outcome": {
            **outcome,
            "accepted_steps_per_stepping_second": outcome["accepted_steps"]
            / outcome["stepping_wall_time_s"],
            "simulated_seconds_per_stepping_second": runtime.state.time_s
            / outcome["stepping_wall_time_s"],
        },
        "quality": quality,
        "pressure": {
            "solve_count": len(pressure_records),
            "total_semantic_inclusive_time_s": sum(
                record.inclusive_ns for record in pressure_ranges
            )
            / 1.0e9,
            "structural_transfer_expectations": pressure_records,
        },
        "phase_wall_time_s": phases,
        "child_observed_wall_time_s": (child_end_ns - child_start_ns) / 1.0e9,
        "child_observed_wall_time_ns": child_end_ns - child_start_ns,
        "child_process_cpu_time_s": (time.process_time_ns() - child_cpu_start_ns)
        / 1.0e9,
        "host_memory": host_memory,
        "device_memory": {
            "cupy_pool_used_bytes_final": int(pool.used_bytes()),
            "cupy_pool_total_bytes_final": int(pool.total_bytes()),
            "cupy_pool_free_bytes_final": int(pool.free_bytes()),
            "cupy_allocation_recorder": device_recorder.to_dict()
            if device_recorder is not None
            else None,
        },
        "archive": {
            "path": relative(logical_archive_path),
            "payloads": archive_payloads(archive_path),
            "strict_reload_pass": True,
            "result_status": reloaded.result.status.value,
            "canonical_numerical_payload": numerical_payload,
        },
        "profile_instrumentation": {
            **instrumentation,
            "semantic_ranges": registry.to_manifest() if registry is not None else None,
            "semantic_wrapper_bindings": {
                name: wrapper.binding for name, wrapper in wrappers.items()
            }
            if wrappers
            else None,
            "host_coverage": semantic_coverage,
            "cprofile_expected_from_launcher": identity["mode"] == "host",
            "nsight_systems_expected_from_launcher": identity["mode"] == "cuda",
        },
    }
    return report


def main(argv: Sequence[str] | None = None) -> int:
    global _ACTIVE_RUNTIME, _BOOTSTRAP_CONTEXT

    args = parse_args(argv)
    output_dir = args.output_dir.resolve()
    try:
        report = run_sample(args)
        atomic_json(output_dir / "sample_result.json", report)
        print(
            json.dumps(
                {
                    "status": report["status"],
                    "sample_id": report["sample_id"],
                    "accepted_steps": report["outcome"]["accepted_steps"],
                    "result": relative(output_dir / "sample_result.json"),
                },
                sort_keys=True,
            ),
            flush=True,
        )
        return 0
    except BaseException as exc:  # noqa: BLE001 - retain evidence for interrupts, too.
        failure = {
            "schema_version": 2,
            "status": "HF3D_P0P2_V2_SAMPLE_FAILED_STOPPED",
            "gate_pass": False,
            "sample_id": str(getattr(args, "sample_id", "UNKNOWN")),
            "failed_at_utc": utc_now(),
            "exception_type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(),
            "partial_evidence": None,
        }
        runtime = _ACTIVE_RUNTIME
        if isinstance(runtime, CaseRuntime) and output_dir.is_dir():
            partial_path = output_dir / "partial.npz"
            if not partial_path.exists():
                try:
                    failure["partial_evidence"] = save_partial(partial_path, runtime)
                except Exception as partial_error:  # noqa: BLE001
                    failure["partial_evidence_error"] = (
                        f"{type(partial_error).__name__}: {partial_error}"
                    )
        if output_dir.is_dir():
            try:
                atomic_json(output_dir / "sample_failure.json", failure)
            except Exception as publication_error:  # noqa: BLE001
                print(
                    "P0-P failure publication failed: "
                    f"{type(publication_error).__name__}: {publication_error}",
                    file=sys.stderr,
                )
        print(json.dumps(failure, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 2
    finally:
        if _BOOTSTRAP_CONTEXT is not None:
            try:
                _BOOTSTRAP_CONTEXT.__exit__(None, None, None)
            except Exception as close_error:  # noqa: BLE001
                print(
                    "P0-P semantic process range close failed: "
                    f"{type(close_error).__name__}: {close_error}",
                    file=sys.stderr,
                )
            _BOOTSTRAP_CONTEXT = None
        _ACTIVE_RUNTIME = None
        gc.collect()


if __name__ == "__main__":
    raise SystemExit(main())
