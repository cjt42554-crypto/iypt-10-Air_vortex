from __future__ import annotations

from dataclasses import replace

from airvortex.core import (
    BackendMode,
    DriveConfig,
    RunConfig,
    SimulationConfig,
)


def forward_smoke_config() -> SimulationConfig:
    base = SimulationConfig()
    return replace(
        base,
        backend=BackendMode.FORWARD,
        drive=DriveConfig(
            initial_rpm=0.0, target_rpm=0.0, ramp_duration_s=0.0
        ),
        run=RunConfig(duration_s=0.01, output_interval_s=0.005),
        forward=replace(
            base.forward,
            integration_time_step_s=0.001,
            surface_radial_points=128,
        ),
    )


def hf3d_smoke_config() -> SimulationConfig:
    base = SimulationConfig()
    return replace(
        base,
        backend=BackendMode.HIGH_FIDELITY,
        drive=DriveConfig(
            initial_rpm=0.0, target_rpm=0.0, ramp_duration_s=0.0
        ),
        run=RunConfig(duration_s=0.0002, output_interval_s=0.0001),
        forward=replace(base.forward, integration_time_step_s=0.0001),
        high_fidelity=replace(
            base.high_fidelity,
            radial_cells=16,
            azimuthal_cells=16,
            axial_cells=24,
            time_step_s=0.0001,
            pressure_relative_tolerance=1.0e-6,
            pressure_max_iterations=800,
        ),
    )
