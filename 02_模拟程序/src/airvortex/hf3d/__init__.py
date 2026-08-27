"""Internal responsibility modules for the formal HF3D backend."""

from .drive import (
    drive_rpm_and_slope,
    integrated_bar_angle_rad,
    rpm_to_omega,
)
from .events import TopologyEventTracker
from .pressure import PressureProjection3D, make_pressure_projection
from .recording import FrameRecorder, StepDiagnosticsBuffer, assemble_result
from .stepper import advance_one_step
from .timestep import TimeStepProposal, propose_time_step, retry_time_step_s
from .types import (
    HF3DPostProjectionCFLExceeded,
    HF3DRejectedStepDiagnostics,
    HF3DState,
    HF3DStepDiagnostics,
    TIME_STEP_LIMITER_CODES,
)


__all__ = [
    "HF3DPostProjectionCFLExceeded",
    "HF3DRejectedStepDiagnostics",
    "HF3DState",
    "HF3DStepDiagnostics",
    "FrameRecorder",
    "StepDiagnosticsBuffer",
    "PressureProjection3D",
    "TIME_STEP_LIMITER_CODES",
    "TimeStepProposal",
    "TopologyEventTracker",
    "drive_rpm_and_slope",
    "advance_one_step",
    "assemble_result",
    "integrated_bar_angle_rad",
    "make_pressure_projection",
    "propose_time_step",
    "retry_time_step_s",
    "rpm_to_omega",
]
