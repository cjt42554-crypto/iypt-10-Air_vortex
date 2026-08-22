#!/usr/bin/env python3
"""Reproducible HF3D pressure-engine benchmark and CPU/CUDA parity record.

This benchmark does not alter the grid, operator, preconditioner, tolerance or
iteration limit between engines.  CUDA warm-up/JIT is excluded from steady
timings.  The host-wrapper timing includes the transfers used by the current
hybrid HF3D time integrator; the device-resident timing describes the upper
bound available after a future all-GPU local-law port.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import statistics
import sys
import time
from typing import Any, Callable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

# Importing airvortex first is intentional: its dependency-free policy sets
# BLAS/OpenMP limits before the package transitively imports NumPy/SciPy.
from airvortex import NUMERICAL_THREAD_POLICY  # noqa: E402
from airvortex.backends.high_fidelity_3d import HighFidelityBackend  # noqa: E402
from airvortex.core import BackendMode, DriveConfig, RunConfig, SimulationConfig  # noqa: E402
from airvortex.numerics import (  # noqa: E402
    CuPyVariableDensityProjection3D,
    ImmersedCartesianGrid,
    VariableDensityProjection3D,
    mixture_property_3d,
    probe_gpu_projection,
)
import numpy as np  # noqa: E402
import scipy  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", choices=("cpu", "cuda", "both"), default="both")
    parser.add_argument("--nx", type=int, default=48)
    parser.add_argument("--ny", type=int, default=48)
    parser.add_argument("--nz", type=int, default=72)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--repeats", type=int, default=7)
    parser.add_argument("--rtol", type=float, default=1.0e-7)
    parser.add_argument("--maxiter", type=int, default=400)
    parser.add_argument(
        "--full-steps",
        type=int,
        default=0,
        help=(
            "set the requested duration to this many configured maximum-dt "
            "intervals; adaptive substeps are counted separately (0 disables)"
        ),
    )
    parser.add_argument("--full-warmups", type=int, default=1)
    parser.add_argument("--full-repeats", type=int, default=3)
    parser.add_argument("--output", type=Path)
    return parser


def _measure(
    operation: Callable[[], Any],
    *,
    warmups: int,
    repeats: int,
    synchronize: Callable[[], None] | None = None,
) -> tuple[list[float], Any]:
    result: Any = None
    for _ in range(warmups):
        result = operation()
        if synchronize is not None:
            synchronize()
    samples: list[float] = []
    for _ in range(repeats):
        if synchronize is not None:
            synchronize()
        start = time.perf_counter()
        result = operation()
        if synchronize is not None:
            synchronize()
        samples.append(time.perf_counter() - start)
    return samples, result


def _summary(samples: list[float]) -> dict[str, object]:
    return {
        "samples_s": samples,
        "median_s": statistics.median(samples),
        "minimum_s": min(samples),
        "maximum_s": max(samples),
    }


def _diagnostics(value: Any) -> dict[str, object]:
    return {
        "iterations": int(value.iterations),
        "relative_residual": float(value.pressure_relative_residual),
        "divergence_before_s_inv": float(value.divergence_before_s_inv),
        "divergence_after_s_inv": float(value.divergence_after_s_inv),
        "active_unknowns": int(value.active_unknowns),
        "matrix_nonzeros": int(value.matrix_nonzeros),
    }


def _full_config(args: argparse.Namespace, device: str) -> SimulationConfig:
    base = SimulationConfig()
    dt_s = 2.0e-4
    duration_s = args.full_steps * dt_s
    return replace(
        base,
        backend=BackendMode.HIGH_FIDELITY,
        drive=DriveConfig(
            initial_rpm=0.0,
            target_rpm=100.0,
            ramp_duration_s=duration_s,
        ),
        run=RunConfig(duration_s=duration_s, output_interval_s=duration_s),
        forward=replace(base.forward, integration_time_step_s=dt_s),
        high_fidelity=replace(
            base.high_fidelity,
            radial_cells=args.nx,
            azimuthal_cells=args.ny,
            axial_cells=args.nz,
            time_step_s=dt_s,
            pressure_solver_device=device,
            pressure_relative_tolerance=args.rtol,
            pressure_max_iterations=args.maxiter,
        ),
    )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.warmups < 0 or args.repeats < 1:
        raise SystemExit("--warmups must be >= 0 and --repeats must be >= 1")
    if args.full_steps < 0 or args.full_warmups < 0 or args.full_repeats < 1:
        raise SystemExit(
            "--full-steps/--full-warmups must be >= 0 and --full-repeats must be >= 1"
        )
    if min(args.nx, args.ny, args.nz) < 8:
        raise SystemExit("every grid count must be >= 8")

    grid = ImmersedCartesianGrid(
        args.nx,
        args.ny,
        args.nz,
        0.045,
        0.080,
        bar_half_length_m=0.015,
        bar_radius_m=0.003,
        bar_centre_z_m=0.0045,
    )
    rng = np.random.default_rng(20260821)
    u_x = rng.normal(scale=1.0e-4, size=grid.face_shape_x) * grid.open_x
    u_y = rng.normal(scale=1.0e-4, size=grid.face_shape_y) * grid.open_y
    u_z = rng.normal(scale=1.0e-4, size=grid.face_shape_z) * grid.open_z
    phase = np.where(grid.z_cell < 0.060, 1.0, 0.0)
    density = mixture_property_3d(phase, 998.2, 1.204)
    common = {
        "relative_tolerance": args.rtol,
        "maximum_iterations": args.maxiter,
    }
    probe = probe_gpu_projection()
    record: dict[str, Any] = {
        "schema": "airvortex-hf3d-hardware-benchmark-v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "equation_change": False,
        "timing_scope": (
            "steady pressure projection; warm-up and CUDA JIT excluded; "
            "host wrapper includes current hybrid transfers"
        ),
        "host": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "numpy": np.__version__,
            "scipy": scipy.__version__,
            "affinity_logical_cpus": len(os.sched_getaffinity(0)),
            "load_average_1_5_15": list(os.getloadavg()),
            "numerical_thread_policy": NUMERICAL_THREAD_POLICY.to_dict(),
        },
        "gpu_probe": probe.to_dict(),
        "case": {
            "grid_x_y_z": [grid.nx, grid.ny, grid.nz],
            "active_cells": grid.active_cell_count,
            "relative_tolerance": args.rtol,
            "maximum_iterations": args.maxiter,
            "random_seed": 20260821,
            "velocity_scale_m_s": 1.0e-4,
            "dt_s": 2.0e-4,
        },
        "engines": {},
    }

    cpu_output: Any | None = None
    if args.device in {"cpu", "both"}:
        cpu = VariableDensityProjection3D(grid, **common)
        samples, cpu_output = _measure(
            lambda: cpu.project(u_x, u_y, u_z, density, 2.0e-4),
            warmups=args.warmups,
            repeats=args.repeats,
        )
        record["engines"]["cpu"] = {
            **_summary(samples),
            "preconditioner": cpu.preconditioner_name,
            "diagnostics": _diagnostics(cpu_output[4]),
        }

    gpu_host_output: Any | None = None
    if args.device in {"cuda", "both"}:
        if not probe.available:
            raise SystemExit(f"CUDA requested but unavailable: {probe.detail}")
        gpu_device = CuPyVariableDensityProjection3D(grid, **common)
        cp = gpu_device.cp
        device_inputs = tuple(cp.asarray(value) for value in (u_x, u_y, u_z, density))
        device_samples, gpu_device_output = _measure(
            lambda: gpu_device.project_device(*device_inputs, 2.0e-4),
            warmups=args.warmups,
            repeats=args.repeats,
            synchronize=gpu_device.synchronize,
        )
        gpu_host = CuPyVariableDensityProjection3D(grid, **common)
        host_samples, gpu_host_output = _measure(
            lambda: gpu_host.project(u_x, u_y, u_z, density, 2.0e-4),
            warmups=args.warmups,
            repeats=args.repeats,
            synchronize=gpu_host.synchronize,
        )
        record["engines"]["cuda_device_resident"] = {
            **_summary(device_samples),
            "preconditioner": gpu_device.preconditioner_name,
            "diagnostics": _diagnostics(gpu_device_output[4]),
        }
        record["engines"]["cuda_hybrid_host_wrapper"] = {
            **_summary(host_samples),
            "preconditioner": gpu_host.preconditioner_name,
            "diagnostics": _diagnostics(gpu_host_output[4]),
        }

    if cpu_output is not None and gpu_host_output is not None:
        cpu_median = record["engines"]["cpu"]["median_s"]
        cuda_median = record["engines"]["cuda_hybrid_host_wrapper"]["median_s"]
        maximum_absolute = [
            float(np.max(np.abs(device - reference)))
            for device, reference in zip(gpu_host_output[:4], cpu_output[:4])
        ]
        record["comparison"] = {
            "hybrid_pressure_speedup": float(cpu_median / cuda_median),
            "maximum_absolute_difference_u_x_u_y_u_z_pressure": maximum_absolute,
            "same_iteration_count": (
                cpu_output[4].iterations == gpu_host_output[4].iterations
            ),
            "absolute_relative_residual_difference": abs(
                cpu_output[4].pressure_relative_residual
                - gpu_host_output[4].pressure_relative_residual
            ),
        }

    if args.full_steps:
        record["full_hf3d"] = {
            "requested_duration_max_dt_intervals": args.full_steps,
            "requested_duration_s": args.full_steps * 2.0e-4,
            "actual_adaptive_steps_are_reported_per_engine": True,
            "includes": (
                "complete backend setup, all local laws, pressure projection, "
                "diagnostics and final result assembly"
            ),
            "engines": {},
        }
        full_results: dict[str, Any] = {}
        for device in (
            item for item in ("cpu", "cuda") if args.device in {item, "both"}
        ):
            config = _full_config(args, device)
            samples, result = _measure(
                lambda config=config: HighFidelityBackend().run(config),
                warmups=args.full_warmups,
                repeats=args.full_repeats,
            )
            full_results[device] = result
            diagnostics = result.metadata["diagnostics"]
            record["full_hf3d"]["engines"][device] = {
                **_summary(samples),
                "status": result.status.value,
                "completed_steps": diagnostics["completed_steps"],
                "pressure_accelerator": diagnostics["pressure_accelerator"],
                "maximum_pressure_relative_residual": diagnostics[
                    "maximum_pressure_relative_residual"
                ],
                "maximum_projected_divergence_s_inv": diagnostics[
                    "maximum_projected_divergence_s_inv"
                ],
            }
        if set(full_results) == {"cpu", "cuda"}:
            cpu_result = full_results["cpu"]
            cuda_result = full_results["cuda"]
            fields = (
                "final_phase_liquid_fraction_3d",
                "final_pressure_gauge_pa_3d",
                "final_u_x_rotating_m_s_3d",
                "final_u_y_rotating_m_s_3d",
                "final_u_z_rotating_m_s_3d",
            )
            record["full_hf3d"]["comparison"] = {
                "end_to_end_speedup": float(
                    record["full_hf3d"]["engines"]["cpu"]["median_s"]
                    / record["full_hf3d"]["engines"]["cuda"]["median_s"]
                ),
                "maximum_absolute_field_difference": {
                    name: float(
                        np.max(
                            np.abs(cuda_result.fields[name] - cpu_result.fields[name])
                        )
                    )
                    for name in fields
                },
            }

    encoded = json.dumps(record, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.output is not None:
        destination = args.output.expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise SystemExit(f"refusing to overwrite existing benchmark: {destination}")
        destination.write_text(encoded, encoding="utf-8")
        print(destination)
    else:
        print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
