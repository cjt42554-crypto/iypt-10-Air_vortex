#!/usr/bin/env python3
"""Synthetic, zero-HF3D-step probe for instrumentation equivalence."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from p0p2v2_contract import FORMAL_SOURCE, atomic_json

if str(FORMAL_SOURCE) not in sys.path:
    sys.path.insert(0, str(FORMAL_SOURCE))

# Apply the formal thread policy before importing NumPy/CuPy.
import airvortex  # noqa: F401
import cupy as cp
import numpy as np
from airvortex._hardware import NUMERICAL_THREAD_POLICY
from p0p2v2_instrumentation import (
    DeviceAllocationRecorder,
    IntervalRegistry,
    TransparentCallable,
)


class CuPyNvtx:
    def push(self, name: str) -> None:
        cp.cuda.nvtx.RangePush(name)

    def pop(self) -> None:
        cp.cuda.nvtx.RangePop()


def numerical_hash(value: Any) -> str:
    array = np.ascontiguousarray(value)
    digest = hashlib.sha256()
    digest.update(array.dtype.str.encode("ascii"))
    digest.update(repr(tuple(array.shape)).encode("ascii"))
    digest.update(array.tobytes(order="C"))
    return digest.hexdigest()


def compute(host: Any) -> Any:
    device = cp.asarray(host)
    transformed = device * cp.float64(2.0) + cp.float64(1.0)
    cp.cuda.runtime.deviceSynchronize()
    return cp.asnumpy(transformed)


def toolchain_identity() -> dict[str, Any]:
    properties = cp.cuda.runtime.getDeviceProperties(0)
    raw_name = properties.get("name")
    device_name = (
        raw_name.decode("utf-8", errors="strict")
        if isinstance(raw_name, bytes)
        else str(raw_name)
    )
    return {
        "gpu_python_realpath": str(Path(sys.executable).resolve()),
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "packages": {
            name: importlib.metadata.version(name)
            for name in ("numpy", "scipy", "cupy-cuda13x")
        },
        "cupy_version": cp.__version__,
        "cuda_driver_version": int(cp.cuda.runtime.driverGetVersion()),
        "cuda_runtime_version": int(cp.cuda.runtime.runtimeGetVersion()),
        "cuda_device_count": int(cp.cuda.runtime.getDeviceCount()),
        "cuda_device_index": 0,
        "cuda_device_name": device_name,
        "cuda_compute_capability": str(cp.cuda.Device(0).compute_capability),
        "cuda_total_global_memory_bytes": int(properties["totalGlobalMem"]),
        "numerical_thread_policy": NUMERICAL_THREAD_POLICY.to_dict(),
        "omp_dynamic": os.environ.get("OMP_DYNAMIC"),
        "affinity": sorted(os.sched_getaffinity(0)),
        "machine": platform.machine(),
        "system": platform.system(),
    }


def run(mode: str) -> dict[str, Any]:
    host = np.arange(1024, dtype=np.float64)
    registry: IntervalRegistry | None = None
    recorder: DeviceAllocationRecorder | None = None
    traced_toolchain: dict[str, Any] | None = None
    if mode == "semantic":
        registry = IntervalRegistry()
        operation = TransparentCallable(
            compute,
            name="synthetic_numpy_cupy_operator",
            registry=registry,
        )
        result = operation(host)
    elif mode == "device-memory":
        recorder = DeviceAllocationRecorder(cp)
        recorder.observe("before")
        with recorder.hook:
            result = compute(host)
        recorder.observe("after")
    elif mode == "cuda-trace":
        registry = IntervalRegistry(nvtx=CuPyNvtx())
        with registry.range("P0P2V2_PROCESS_START"):
            with registry.range("P0P2V2_DEVICE_ALLOCATION_SCOPE"):
                with registry.range("pressure.project") as call_id:
                    result = compute(host)
                with registry.range("P0P2V2_FINAL_SYNC_END"):
                    cp.cuda.runtime.deviceSynchronize()
            # Keep every CUDA runtime query made by this probe inside the
            # process-level range while leaving the allocation scope limited
            # to the measured compute and its final synchronization.
            traced_toolchain = toolchain_identity()
        if call_id != 3:
            raise RuntimeError("synthetic pressure call ID drifted")
    elif mode == "uninstrumented":
        result = compute(host)
    else:
        raise ValueError("unknown capability probe mode")
    expected = host * 2.0 + 1.0
    if not np.array_equal(result, expected):
        raise RuntimeError("synthetic numerical result changed")
    return {
        "schema_version": 2,
        "status": "P0P2V2_ZERO_STEP_CAPABILITY_PROBE_PASS",
        "mode": mode,
        "pid": os.getpid(),
        "numerical_sha256": numerical_hash(result),
        "input_nbytes": int(host.nbytes),
        "output_nbytes": int(result.nbytes),
        "expected_pressure_transfers": [
            {
                "call_id": 3,
                "expected_h2d_size_multiset": [int(host.nbytes)],
                "expected_d2h_size_multiset": [int(result.nbytes)],
            }
        ]
        if mode == "cuda-trace"
        else [],
        "semantic_ranges": registry.to_manifest() if registry is not None else None,
        "device_allocation_recorder": recorder.to_dict()
        if recorder is not None
        else None,
        "toolchain_identity": traced_toolchain or toolchain_identity(),
        "hf3d_time_steps_advanced": 0,
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=("uninstrumented", "semantic", "device-memory", "cuda-trace"),
        required=True,
    )
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.output.exists() or args.output.is_symlink():
        return 2
    report = run(args.mode)
    atomic_json(args.output, report)
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
