"""Exact prescribed-drive kinematics used by HF3D."""

from __future__ import annotations

import math

import numpy as np

from airvortex.core import SimulationConfig


def rpm_to_omega(rpm: float) -> float:
    return 2.0 * math.pi * rpm / 60.0


def drive_rpm_and_slope(
    config: SimulationConfig, time_s: float
) -> tuple[float, float]:
    """Piecewise-linear actual signed bar rpm and its right-hand slope."""

    drive = config.drive
    t = float(np.clip(time_s, 0.0, config.run.duration_s))
    if drive.actual_speed_schedule_time_s:
        times = np.asarray(drive.actual_speed_schedule_time_s, dtype=np.float64)
        speeds = np.asarray(drive.actual_speed_schedule_rpm, dtype=np.float64)
        if t >= times[-1]:
            return float(speeds[-1]), 0.0
        index = int(np.searchsorted(times, t, side="right") - 1)
        index = max(0, min(index, times.size - 2))
        duration = times[index + 1] - times[index]
        slope = (speeds[index + 1] - speeds[index]) / duration
        return float(speeds[index] + slope * (t - times[index])), float(slope)
    direction = float(drive.rotation_direction)
    if drive.ramp_duration_s > 0.0 and t < drive.ramp_duration_s:
        slope = direction * (
            drive.target_rpm - drive.initial_rpm
        ) / drive.ramp_duration_s
        return float(direction * drive.initial_rpm + slope * t), float(slope)
    return float(direction * drive.target_rpm), 0.0


def integrated_bar_angle_rad(config: SimulationConfig, time_s: float) -> float:
    """Exact integral of the configured piecewise-linear actual bar speed."""

    drive = config.drive
    t = max(0.0, float(time_s))
    rpm_seconds = 0.0
    if drive.actual_speed_schedule_time_s:
        times = drive.actual_speed_schedule_time_s
        speeds = drive.actual_speed_schedule_rpm
        for left, right, rpm_left, rpm_right in zip(
            times[:-1], times[1:], speeds[:-1], speeds[1:]
        ):
            if t <= left:
                break
            width = min(t, right) - left
            if width <= 0.0:
                continue
            slope = (rpm_right - rpm_left) / (right - left)
            rpm_seconds += rpm_left * width + 0.5 * slope * width * width
            if t <= right:
                break
        if t > times[-1]:
            rpm_seconds += speeds[-1] * (t - times[-1])
    else:
        signed_initial = drive.rotation_direction * drive.initial_rpm
        signed_target = drive.rotation_direction * drive.target_rpm
        ramp = drive.ramp_duration_s
        if ramp > 0.0:
            width = min(t, ramp)
            slope = (signed_target - signed_initial) / ramp
            rpm_seconds += signed_initial * width + 0.5 * slope * width * width
            if t > ramp:
                rpm_seconds += signed_target * (t - ramp)
        else:
            rpm_seconds = signed_target * t
    return rpm_seconds * (2.0 * math.pi / 60.0)


_rpm_to_omega = rpm_to_omega
_drive_rpm_and_slope = drive_rpm_and_slope
_integrated_bar_angle_rad = integrated_bar_angle_rad


__all__ = [
    "drive_rpm_and_slope",
    "integrated_bar_angle_rad",
    "rpm_to_omega",
]
