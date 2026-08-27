"""Architecture and compatibility contracts for the bounded HF3D refactor."""

from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path
import unittest

import numpy as np

import airvortex.backends.high_fidelity_3d as compatibility
from airvortex.backends.high_fidelity_3d import HighFidelityBackend
from airvortex.core import (
    BackendMode,
    Control,
    DriveConfig,
    RunConfig,
    RunStatus,
    SimulationConfig,
)
from airvortex.hf3d import (
    HF3DPostProjectionCFLExceeded,
    HF3DRejectedStepDiagnostics,
    HF3DState,
    HF3DStepDiagnostics,
    StepDiagnosticsBuffer,
    TIME_STEP_LIMITER_CODES,
    TopologyEventTracker,
    drive_rpm_and_slope,
    integrated_bar_angle_rad,
    make_pressure_projection,
    retry_time_step_s,
)
from airvortex.numerics.interface3d import Topology3DDiagnostics


def topology(
    *,
    touches_bar: bool = False,
    resolved_channel: bool = False,
    minimum_throat_radius_m: float = 0.0,
) -> Topology3DDiagnostics:
    return Topology3DDiagnostics(
        touches_bar=touches_bar,
        finite_channel_candidate=resolved_channel,
        resolved_channel=resolved_channel,
        bar_gas_contact_area_m2=1.0e-6 if touches_bar else 0.0,
        minimum_throat_radius_m=minimum_throat_radius_m,
        required_resolved_radius_m=1.0e-3,
        minimum_gap_to_bar_m=0.0 if touches_bar else 1.0e-3,
        isolated_gas_volume_m3=0.0,
        top_connected_gas_volume_m3=1.0e-8,
        throat_radius_by_height_m=np.asarray([minimum_throat_radius_m]),
        top_connected_gas_mask=np.zeros((1, 1, 1), dtype=bool),
    )


def diagnostics(
    *,
    topology_value: Topology3DDiagnostics | None = None,
    rpm: float = 100.0,
) -> HF3DStepDiagnostics:
    return HF3DStepDiagnostics(
        dt_s=1.0e-4,
        initial_attempt_dt_s=1.0e-4,
        time_step_limiter="registered_time_step",
        cfl_retry_count=0,
        starting_advective_cfl=0.01,
        pre_source_advective_cfl=0.02,
        pre_projection_advective_cfl=0.03,
        maximum_rejected_post_projection_cfl=0.0,
        actual_bar_rpm=rpm,
        angular_velocity_rad_s=1.0,
        angular_acceleration_rad_s2=2.0,
        advective_cfl=0.04,
        viscous_cfl=0.05,
        capillary_cfl=0.06,
        rotation_increment_rad=1.0e-4,
        divergence_before_s_inv=0.1,
        divergence_after_s_inv=1.0e-5,
        pressure_relative_residual=1.0e-8,
        pressure_iterations=2,
        liquid_volume_m3=1.0,
        expected_liquid_volume_m3=1.0,
        liquid_volume_balance_error_m3=0.0,
        phase_top_outward_flux_m3_s=0.0,
        phase_clip_absolute_volume_m3=0.0,
        phase_repair_absolute_volume_m3=0.0,
        kinetic_energy_lab_j=1.0,
        kinetic_energy_rotating_j=1.0,
        angular_momentum_z_kg_m2_s=1.0,
        viscous_dissipation_w=1.0,
        maximum_surface_tension_acceleration_m_s2=1.0,
        maximum_momentum_advection_acceleration_m_s2=1.0,
        maximum_viscous_acceleration_m_s2=1.0,
        maximum_boundary_acceleration_m_s2=1.0,
        maximum_rotating_frame_acceleration_m_s2=1.0,
        gravity_acceleration_m_s2=9.80665,
        maximum_pressure_projection_acceleration_m_s2=1.0,
        boundary_rotating_energy_change_j=0.0,
        topology=topology() if topology_value is None else topology_value,
    )


def small_runtime_config(*, initial_rpm: float = 0.0) -> SimulationConfig:
    base = SimulationConfig()
    return replace(
        base,
        backend=BackendMode.HIGH_FIDELITY,
        drive=DriveConfig(
            initial_rpm=initial_rpm,
            target_rpm=max(initial_rpm, 100.0),
            ramp_duration_s=0.0,
            rotation_direction=1,
        ),
        run=RunConfig(duration_s=2.0e-4, output_interval_s=2.0e-4),
        forward=replace(base.forward, integration_time_step_s=1.0e-4),
        high_fidelity=replace(
            base.high_fidelity,
            radial_cells=8,
            azimuthal_cells=8,
            axial_cells=10,
            time_step_s=1.0e-4,
            pressure_solver_device="cpu",
            pressure_relative_tolerance=1.0e-7,
            pressure_max_iterations=800,
            interface_compression_interval=0,
        ),
    )


class HF3DRefactorCompatibilityTests(unittest.TestCase):
    def test_legacy_module_reexports_the_same_contract_objects(self) -> None:
        self.assertIs(compatibility.HF3DState, HF3DState)
        self.assertIs(
            compatibility.HF3DStepDiagnostics,
            HF3DStepDiagnostics,
        )
        self.assertIs(
            compatibility.HF3DRejectedStepDiagnostics,
            HF3DRejectedStepDiagnostics,
        )
        self.assertIs(
            compatibility.HF3DPostProjectionCFLExceeded,
            HF3DPostProjectionCFLExceeded,
        )
        self.assertIs(compatibility._drive_rpm_and_slope, drive_rpm_and_slope)
        self.assertIs(
            compatibility._integrated_bar_angle_rad,
            integrated_bar_angle_rad,
        )
        self.assertIs(
            compatibility._make_pressure_projection,
            make_pressure_projection,
        )
        self.assertIs(compatibility._retry_time_step_s, retry_time_step_s)

    def test_compatibility_entry_is_orchestration_not_a_second_solver(self) -> None:
        source_path = Path(compatibility.__file__).resolve()
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        backend = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "HighFidelityBackend"
        )
        methods = {
            node.name: node
            for node in backend.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        advance_calls = {
            node.func.id
            for node in ast.walk(methods["advance_one_step"])
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertEqual(advance_calls, {"_advance_one_step"})
        run_calls = {
            node.func.id
            for node in ast.walk(methods["run"])
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        for required in (
            "FrameRecorder",
            "StepDiagnosticsBuffer",
            "TopologyEventTracker",
            "assemble_result",
        ):
            self.assertIn(required, run_calls)
        nested_functions = [
            node
            for node in ast.iter_child_nodes(methods["run"])
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        self.assertEqual(nested_functions, [])

    def test_step_buffer_keeps_named_dense_diagnostics_and_limiter_identity(self) -> None:
        config = SimulationConfig()
        buffer = StepDiagnosticsBuffer(initial_liquid_volume_m3=1.0)
        item = diagnostics()
        buffer.append(1.0e-4, item, config)
        self.assertEqual(buffer.step_time, [1.0e-4])
        self.assertEqual(buffer.step_dt, [item.dt_s])
        self.assertEqual(
            buffer.step_time_limiter_code,
            [float(TIME_STEP_LIMITER_CODES["registered_time_step"])],
        )
        self.assertEqual(buffer.limiter_counts["registered_time_step"], 1)
        self.assertEqual(buffer.maximum_cfl, item.advective_cfl)
        self.assertEqual(buffer.maximum_divergence, item.divergence_after_s_inv)

    def test_topology_event_sequence_and_payload_are_stable(self) -> None:
        config = SimulationConfig()
        events = []
        tracker = TopologyEventTracker(config, events)
        tracker.initialise(topology())
        connected = topology(
            touches_bar=True,
            resolved_channel=True,
            minimum_throat_radius_m=0.002,
        )
        tracker.update(1.0, diagnostics(topology_value=connected, rpm=6000.0))
        tracker.update(2.0, diagnostics(topology_value=topology(), rpm=6000.0))
        tracker.update(3.0, diagnostics(topology_value=connected, rpm=6000.0))
        tracker.update(3.6, diagnostics(topology_value=connected, rpm=6000.0))
        self.assertEqual(
            [event.kind for event in events],
            [
                "gas_touches_bar",
                "resolved_channel",
                "channel_break",
                "gas_touches_bar",
                "channel_reconnect",
                "sustained_channel_numerical",
            ],
        )
        self.assertEqual(
            events[1].data["minimum_throat_radius_m"],
            connected.minimum_throat_radius_m,
        )
        self.assertEqual(events[-1].data["required_hold_time_s"], 0.5)

    def test_nonzero_initial_speed_performs_the_initial_projection(self) -> None:
        result = HighFidelityBackend().run(
            small_runtime_config(initial_rpm=100.0)
        )
        diagnostics_record = result.metadata["diagnostics"]
        self.assertEqual(
            diagnostics_record["pressure_solves"],
            diagnostics_record["completed_steps"] + 1,
        )
        self.assertEqual(
            diagnostics_record["pressure_accelerator"]["selected"],
            "cpu",
        )

    def test_soft_stop_keeps_one_committed_step_and_event_contract(self) -> None:
        class StopAfterOneAcceptedStep(Control):
            def __init__(self) -> None:
                super().__init__()
                self.calls = 0

            def checkpoint(self) -> bool:
                self.calls += 1
                if self.calls == 2:
                    self.request_stop("refactor characterization stop")
                return super().checkpoint()

        result = HighFidelityBackend().run(
            small_runtime_config(),
            control=StopAfterOneAcceptedStep(),
        )
        self.assertIs(result.status, RunStatus.STOPPED)
        self.assertEqual(result.metadata["diagnostics"]["completed_steps"], 1)
        self.assertEqual([event.kind for event in result.events], ["soft_stop"])
        self.assertEqual(
            result.events[0].message,
            "refactor characterization stop",
        )


if __name__ == "__main__":
    unittest.main()
