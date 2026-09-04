"""Optional CUDA parity gates; skipped cleanly without an isolated CuPy runtime."""

from __future__ import annotations

from dataclasses import replace
import unittest

import numpy as np

from airvortex.backends.high_fidelity_3d import HighFidelityBackend
from airvortex.core import BackendMode, DriveConfig, RunConfig, RunStatus, SimulationConfig
from airvortex.numerics import (
    CuPyVariableDensityProjection3D,
    ImmersedCartesianGrid,
    VariableDensityProjection3D,
    mixture_property_3d,
    probe_gpu_projection,
)


PROBE = probe_gpu_projection()


class GPUProjectionAvailabilityTests(unittest.TestCase):
    def test_probe_is_fail_closed_and_structured(self) -> None:
        payload = PROBE.to_dict()
        self.assertIsInstance(payload["available"], bool)
        self.assertTrue(payload["detail"])


@unittest.skipUnless(PROBE.available, PROBE.detail)
class CuPyProjectionParityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.grid = ImmersedCartesianGrid(
            12,
            12,
            16,
            0.045,
            0.080,
            bar_half_length_m=0.015,
            bar_radius_m=0.003,
            bar_centre_z_m=0.0045,
        )

    def test_device_matrix_and_face_coefficients_match_cpu_operator(self) -> None:
        rng = np.random.default_rng(20260827)
        density = rng.uniform(1.204, 998.2, size=self.grid.shape)
        cpu = VariableDensityProjection3D(
            self.grid, relative_tolerance=1.0e-9, maximum_iterations=1200
        )
        gpu = CuPyVariableDensityProjection3D(
            self.grid, relative_tolerance=1.0e-9, maximum_iterations=1200
        )
        cpu_values = cpu._matrix_and_face_beta(density)
        gpu_values = gpu._matrix_and_face_beta_device(gpu.cp.asarray(density))
        np.testing.assert_array_equal(
            gpu.cp.asnumpy(gpu_values[0].indptr), cpu_values[0].indptr
        )
        np.testing.assert_array_equal(
            gpu.cp.asnumpy(gpu_values[0].indices), cpu_values[0].indices
        )
        np.testing.assert_allclose(
            gpu.cp.asnumpy(gpu_values[0].data),
            cpu_values[0].data,
            rtol=2.0e-15,
            atol=0.0,
        )
        for device, reference in zip(gpu_values[1:], cpu_values[1:]):
            np.testing.assert_allclose(
                gpu.cp.asnumpy(device), reference, rtol=2.0e-15, atol=0.0
            )

    def test_fp64_projection_reaches_same_discrete_residual_and_divergence_gate(self) -> None:
        rng = np.random.default_rng(20260828)
        ux = rng.normal(scale=0.02, size=self.grid.face_shape_x) * self.grid.open_x
        uy = rng.normal(scale=0.02, size=self.grid.face_shape_y) * self.grid.open_y
        uz = rng.normal(scale=0.02, size=self.grid.face_shape_z) * self.grid.open_z
        phase = np.where(self.grid.z_cell < 0.060, 1.0, 0.0)
        density = mixture_property_3d(phase, 998.2, 1.204)
        cpu = VariableDensityProjection3D(
            self.grid, relative_tolerance=1.0e-9, maximum_iterations=1600, maximum_divergence_s_inv=1.0e-8
        )
        gpu = CuPyVariableDensityProjection3D(
            self.grid, relative_tolerance=1.0e-9, maximum_iterations=1600, maximum_divergence_s_inv=1.0e-8
        )
        cpu_result = cpu.project(ux, uy, uz, density, 1.0e-4)
        gpu_result = gpu.project(ux, uy, uz, density, 1.0e-4)
        for result in (cpu_result, gpu_result):
            self.assertLessEqual(result[4].divergence_after_s_inv, 1.0e-8)
            self.assertLess(result[4].divergence_residual_identity_error_s_inv, 1.0e-10)
        self.assertLess(cpu_result[4].pressure_relative_residual, 1.1e-9)
        self.assertLess(gpu_result[4].pressure_relative_residual, 1.1e-9)
        self.assertLess(
            gpu_result[4].divergence_after_s_inv,
            2.0e-8 * gpu_result[4].divergence_before_s_inv,
        )
        for device, reference in zip(gpu_result[:4], cpu_result[:4]):
            np.testing.assert_allclose(device, reference, rtol=2.0e-8, atol=2.0e-10)

    def test_device_pressure_history_checkpoint_restores_rejected_trial(self) -> None:
        rng = np.random.default_rng(20260829)
        ux = rng.normal(scale=0.02, size=self.grid.face_shape_x) * self.grid.open_x
        uy = rng.normal(scale=0.02, size=self.grid.face_shape_y) * self.grid.open_y
        uz = rng.normal(scale=0.02, size=self.grid.face_shape_z) * self.grid.open_z
        density = np.where(self.grid.z_cell < 0.060, 998.2, 1.204)
        gpu = CuPyVariableDensityProjection3D(
            self.grid, relative_tolerance=1.0e-9, maximum_iterations=1600, maximum_divergence_s_inv=1.0e-8
        )
        *_, pressure, _ = gpu.project(ux, uy, uz, density, 1.0e-4)
        checkpoint = gpu.checkpoint_history()
        self.assertIs(
            checkpoint.last_pressure_solution, gpu._last_pressure_solution
        )
        gpu.project(
            1.1 * ux,
            1.1 * uy,
            1.1 * uz,
            density,
            8.0e-5,
            initial_pressure_pa=pressure,
        )
        self.assertGreater(gpu.solve_count, checkpoint.solve_count)
        gpu.restore_history(checkpoint)
        self.assertEqual(gpu.solve_count, checkpoint.solve_count)
        self.assertIs(
            gpu._last_pressure_solution, checkpoint.last_pressure_solution
        )
        self.assertIs(
            gpu._previous_pressure_solution,
            checkpoint.previous_pressure_solution,
        )

    def test_one_complete_hf3d_step_matches_explicit_cpu_path(self) -> None:
        base = SimulationConfig()
        common = replace(
            base,
            backend=BackendMode.HIGH_FIDELITY,
            drive=DriveConfig(
                initial_rpm=0.0,
                target_rpm=100.0,
                ramp_duration_s=1.0e-4,
            ),
            run=RunConfig(duration_s=1.0e-4, output_interval_s=1.0e-4),
            forward=replace(base.forward, integration_time_step_s=1.0e-4),
            high_fidelity=replace(
                base.high_fidelity,
                radial_cells=8,
                azimuthal_cells=8,
                axial_cells=10,
                time_step_s=1.0e-4,
                pressure_relative_tolerance=1.0e-7,
                pressure_max_iterations=800,
                interface_compression_interval=0,
            ),
        )
        cpu_config = replace(
            common,
            high_fidelity=replace(
                common.high_fidelity, pressure_solver_device="cpu"
            ),
        )
        cuda_config = replace(
            common,
            high_fidelity=replace(
                common.high_fidelity, pressure_solver_device="cuda"
            ),
        )
        cpu = HighFidelityBackend().run(cpu_config)
        cuda = HighFidelityBackend().run(cuda_config)
        self.assertIs(cpu.status, RunStatus.COMPLETED)
        self.assertIs(cuda.status, RunStatus.COMPLETED)
        self.assertEqual(
            cpu.metadata["diagnostics"]["pressure_accelerator"]["selected"],
            "cpu",
        )
        self.assertEqual(
            cuda.metadata["diagnostics"]["pressure_accelerator"]["selected"],
            "cuda",
        )
        for name in (
            "final_phase_liquid_fraction_3d",
            "final_pressure_gauge_pa_3d",
            "final_u_x_rotating_m_s_3d",
            "final_u_y_rotating_m_s_3d",
            "final_u_z_rotating_m_s_3d",
        ):
            np.testing.assert_allclose(
                cuda.fields[name], cpu.fields[name], rtol=2.0e-8, atol=2.0e-10
            )


if __name__ == "__main__":
    unittest.main()
