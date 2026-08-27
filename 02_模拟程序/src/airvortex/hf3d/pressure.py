"""Fail-closed CPU/CUDA pressure-projection selection for HF3D."""

from __future__ import annotations

from airvortex.core import SimulationConfig
from airvortex.numerics.cartesian3d import ImmersedCartesianGrid
from airvortex.numerics.projection3d import VariableDensityProjection3D
from airvortex.numerics.projection3d_gpu import (
    CuPyVariableDensityProjection3D,
    GPUProjectionUnavailable,
    probe_gpu_projection,
)


PressureProjection3D = VariableDensityProjection3D | CuPyVariableDensityProjection3D


def make_pressure_projection(
    config: SimulationConfig,
    grid: ImmersedCartesianGrid,
) -> tuple[PressureProjection3D, dict[str, object]]:
    """Select a reproducible pressure engine without changing its operator.

    ``auto`` falls back only during availability/construction probing. Once a
    run has started on CUDA, a device failure remains fail-closed instead of
    silently switching floating-point reduction order partway through a run.
    """

    requested = config.high_fidelity.pressure_solver_device
    common = {
        "relative_tolerance": config.high_fidelity.pressure_relative_tolerance,
        "maximum_iterations": config.high_fidelity.pressure_max_iterations,
    }
    if requested == "cpu":
        return VariableDensityProjection3D(grid, **common), {
            "requested": requested,
            "selected": "cpu",
            "fallback": False,
            "detail": "CPU reference projection selected explicitly",
            "device_name": None,
            "compute_capability": None,
            "host_device_transfer_each_solve": False,
        }

    probe = probe_gpu_projection()
    if probe.available:
        try:
            projection = CuPyVariableDensityProjection3D(grid, **common)
        except (GPUProjectionUnavailable, RuntimeError, OSError) as exc:
            if requested == "cuda":
                raise RuntimeError(
                    "HF3D CUDA pressure solver was requested but construction failed: "
                    f"{type(exc).__name__}: {exc}"
                ) from exc
            detail = (
                "CUDA probe passed but construction failed; using CPU: "
                f"{type(exc).__name__}: {exc}"
            )
        else:
            return projection, {
                "requested": requested,
                "selected": "cuda",
                "fallback": False,
                "detail": probe.detail,
                "device_name": probe.device_name,
                "compute_capability": probe.compute_capability,
                "total_memory_bytes": probe.total_memory_bytes,
                "host_device_transfer_each_solve": True,
            }
    else:
        if requested == "cuda":
            raise RuntimeError(
                "HF3D CUDA pressure solver was requested but is unavailable: "
                f"{probe.detail}"
            )
        detail = f"CUDA unavailable; using CPU: {probe.detail}"

    return VariableDensityProjection3D(grid, **common), {
        "requested": requested,
        "selected": "cpu",
        "fallback": True,
        "detail": detail,
        "device_name": probe.device_name,
        "compute_capability": probe.compute_capability,
        "total_memory_bytes": probe.total_memory_bytes,
        "host_device_transfer_each_solve": False,
    }


_make_pressure_projection = make_pressure_projection


__all__ = ["PressureProjection3D", "make_pressure_projection"]
