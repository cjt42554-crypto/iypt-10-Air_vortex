"""Numerical gates for the executable H1 HF-preview backend."""

from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path
import unittest

import numpy as np

from airvortex.backends.high_fidelity import HighFidelityBackend, MODEL_NAME
from airvortex.core import (
    BackendMode,
    DriveConfig,
    HighFidelityConfig,
    RunConfig,
    RunStatus,
    SimulationConfig,
)
from airvortex.numerics import (
    AxisymmetricGrid,
    AxisymmetricProjection,
    conservative_advect_scalar,
    conservative_cls_compress,
    gas_connection_diagnostics,
    initialise_flat_phase,
    initialise_rotating_bucket_phase,
    solid_body_surface_profile,
    surface_profile,
)


def small_config(
    *,
    target_rpm: float = 600.0,
    direction: int = 1,
    duration_s: float = 0.02,
) -> SimulationConfig:
    base = SimulationConfig()
    return replace(
        base,
        backend=BackendMode.HIGH_FIDELITY,
        high_fidelity=HighFidelityConfig(
            radial_cells=16,
            azimuthal_cells=16,
            axial_cells=24,
            time_step_s=5.0e-4,
            cfl_limit=0.40,
            interface_thickness_cells=2.0,
            pressure_relative_tolerance=1.0e-7,
            pressure_max_iterations=400,
        ),
        run=RunConfig(
            duration_s=duration_s,
            output_interval_s=duration_s / 2.0,
        ),
        drive=DriveConfig(
            initial_rpm=0.0,
            target_rpm=target_rpm,
            ramp_duration_s=duration_s / 2.0 if duration_s > 0.0 else 0.0,
            rotation_direction=direction,
        ),
    )


class ProjectionTests(unittest.TestCase):
    def test_projection_reduces_discrete_divergence_and_reuses_factor(self) -> None:
        grid = AxisymmetricGrid(16, 24, 0.045, 0.075)
        rng = np.random.default_rng(20270820)
        u_r = rng.normal(scale=0.1, size=(grid.nr + 1, grid.nz))
        u_z = rng.normal(scale=0.1, size=(grid.nr, grid.nz + 1))
        u_r[[0, -1], :] = 0.0
        u_z[:, [0, -1]] = 0.0
        solver = AxisymmetricProjection(grid)

        projected_r, projected_z, _, diagnostics = solver.project(
            u_r, u_z, 1.0e-3, 998.2
        )
        self.assertGreater(diagnostics.divergence_before, 1.0)
        self.assertLess(
            diagnostics.divergence_after,
            1.0e-8 * diagnostics.divergence_before,
        )
        self.assertLess(diagnostics.pressure_relative_residual, 1.0e-9)
        self.assertEqual(solver.factorization_count, 1)

        # A second projection uses the same SuperLU object/factorisation.
        solver.project(projected_r, projected_z, 1.0e-3, 998.2)
        self.assertEqual(solver.factorization_count, 1)


class ConservativeTransportTests(unittest.TestCase):
    def test_velocity_reversal_approximately_recovers_scalar(self) -> None:
        grid = AxisymmetricGrid(20, 24, 0.05, 0.06)
        r_vertices = grid.r_faces[:, None]
        z_vertices = grid.z_faces[None, :]
        # A streamfunction which is zero on every boundary gives a discretely
        # divergence-free closed recirculation on the cylindrical MAC grid.
        psi = (
            r_vertices**2
            * (grid.radius_m - r_vertices) ** 2
            * np.sin(np.pi * z_vertices / grid.height_m) ** 2
        )
        u_r = np.zeros((grid.nr + 1, grid.nz), dtype=np.float64)
        u_z = np.zeros((grid.nr, grid.nz + 1), dtype=np.float64)
        u_r[1:-1, :] = -(
            psi[1:-1, 1:] - psi[1:-1, :-1]
        ) / (grid.dz * grid.r_faces[1:-1, None])
        u_z[:, :] = (
            psi[1:, :] - psi[:-1, :]
        ) / (grid.dr * grid.r_centres[:, None])
        velocity_scale = 0.018 / max(np.max(np.abs(u_r)), np.max(np.abs(u_z)))
        u_r *= velocity_scale
        u_z *= velocity_scale
        self.assertLess(np.max(np.abs(grid.divergence(u_r, u_z))), 1.0e-11)

        rr, zz = np.meshgrid(grid.r_centres, grid.z_centres, indexing="ij")
        initial = np.exp(
            -((rr - 0.022) / 0.009) ** 2 - ((zz - 0.032) / 0.011) ** 2
        )
        dt = 0.004
        forward = conservative_advect_scalar(initial, u_r, u_z, grid, dt)
        reversed_scalar = conservative_advect_scalar(
            forward, -u_r, -u_z, grid, dt
        )
        relative_l2 = np.linalg.norm(reversed_scalar - initial) / np.linalg.norm(initial)
        self.assertLess(relative_l2, 0.03)
        self.assertAlmostEqual(
            grid.integrate(reversed_scalar), grid.integrate(initial), places=14
        )

    def test_conservative_cls_compression_has_an_auditable_mass_budget(self) -> None:
        grid = AxisymmetricGrid(24, 40, 0.045, 0.075)
        cell_size = min(grid.dr, grid.dz)
        phase = initialise_flat_phase(grid, 0.055, 4.0 * cell_size)
        initial_volume = grid.integrate(phase)
        diffuse_measure_before = grid.integrate(phase * (1.0 - phase))

        compressed, diagnostics = conservative_cls_compress(
            phase,
            grid,
            2.0 * cell_size,
            pseudo_cfl=0.04,
            iterations=100,
        )
        diffuse_measure_after = grid.integrate(compressed * (1.0 - compressed))
        self.assertLess(diffuse_measure_after, 0.9 * diffuse_measure_before)
        self.assertAlmostEqual(grid.integrate(compressed), initial_volume, places=14)
        self.assertAlmostEqual(
            diagnostics.final_integral,
            diagnostics.clipped_integral
            + diagnostics.mass_repair_signed_integral,
            places=14,
        )
        self.assertGreaterEqual(diagnostics.clip_absolute_integral, 0.0)
        self.assertGreaterEqual(diagnostics.mass_repair_absolute_integral, 0.0)

    def test_over_cfl_transport_exposes_clipping_and_mass_repair(self) -> None:
        grid = AxisymmetricGrid(8, 8, 0.040, 0.040)
        phase = np.zeros(grid.shape, dtype=np.float64)
        phase[:4, :4] = 1.0
        u_r = np.zeros((grid.nr + 1, grid.nz), dtype=np.float64)
        u_z = np.zeros((grid.nr, grid.nz + 1), dtype=np.float64)
        u_r[1:-1, :] = 3.0
        _, diagnostics = conservative_advect_scalar(
            phase,
            u_r,
            u_z,
            grid,
            0.01,
            return_diagnostics=True,
        )
        self.assertGreater(diagnostics.clip_absolute_integral, 0.0)
        self.assertGreater(diagnostics.mass_repair_absolute_integral, 0.0)
        self.assertLess(diagnostics.raw_minimum, 0.0)
        self.assertGreater(diagnostics.raw_maximum, 1.0)


class InterfaceGateTests(unittest.TestCase):
    def test_connection_needs_resolution_and_interface_thickness_gate(self) -> None:
        grid = AxisymmetricGrid(8, 12, 0.040, 0.060)
        phase = np.ones(grid.shape, dtype=np.float64)
        phase[:, -2:] = 0.0
        phase[0, :] = 0.0
        one_cell = gas_connection_diagnostics(phase, grid, 0.015, 0.010)
        self.assertTrue(one_cell.one_cell_precursor)
        self.assertFalse(one_cell.resolved_candidate)
        self.assertEqual(one_cell.minimum_throat_cells, 1)

        self.assertEqual(one_cell.required_throat_cells, 4)

        phase[1:4, :] = 0.0
        four_cells = gas_connection_diagnostics(phase, grid, 0.015, 0.010)
        self.assertTrue(four_cells.resolved_candidate)
        self.assertEqual(four_cells.required_throat_cells, 4)
        self.assertEqual(four_cells.minimum_throat_cells, 4)

        thick_interface = gas_connection_diagnostics(
            phase,
            grid,
            0.015,
            0.010,
            interface_thickness_m=3.0 * grid.dr,
        )
        self.assertFalse(thick_interface.resolved_candidate)
        self.assertEqual(thick_interface.required_throat_cells, 6)
        phase[4:6, :] = 0.0
        six_cells = gas_connection_diagnostics(
            phase,
            grid,
            0.015,
            0.010,
            interface_thickness_m=3.0 * grid.dr,
        )
        self.assertTrue(six_cells.resolved_candidate)
        self.assertEqual(
            six_cells.temporal_classification,
            "instantaneous_candidate_only",
        )

    def test_known_rotating_bucket_surface_and_grid_trend(self) -> None:
        errors: list[float] = []
        for radial_cells in (12, 24, 48):
            grid = AxisymmetricGrid(
                radial_cells, 2 * radial_cells, 0.045, 0.080
            )
            analytic = solid_body_surface_profile(grid, 0.055, 10.0, 9.81)
            phase = initialise_rotating_bucket_phase(
                grid,
                0.055,
                10.0,
                9.81,
                1.5 * min(grid.dr, grid.dz),
            )
            extracted = surface_profile(phase, grid)
            errors.append(float(np.sqrt(np.mean((extracted - analytic) ** 2))))
            radial_weights = grid.r_centres
            self.assertAlmostEqual(
                float(
                    np.sum(radial_weights * analytic) / np.sum(radial_weights)
                ),
                0.055,
                places=14,
            )
        self.assertLess(errors[1], 0.55 * errors[0])
        self.assertLess(errors[2], 0.55 * errors[1])


class HighFidelityBackendTests(unittest.TestCase):
    def test_shared_surface_depth_uses_physical_outer_and_central_rois(self) -> None:
        config = small_config(target_rpm=0.0)
        backend = HighFidelityBackend()
        grid = backend.make_grid(config)
        state = backend.initialise_state(config, grid)
        state.phase = initialise_rotating_bucket_phase(
            grid,
            config.container.liquid_depth_m,
            8.0,
            config.fluid.gravity_m_s2,
            1.5 * min(grid.dr, grid.dz),
        )
        initial_volume = grid.integrate(state.phase)
        observed = backend._surface_observables(
            state, config, grid, initial_volume
        )
        profile = surface_profile(state.phase, grid)
        outer = grid.r_centres >= 0.8 * config.container.radius_m
        centre = grid.r_centres <= config.stir_bar.half_length_m
        expected = float(np.mean(profile[outer]) - np.min(profile[centre]))
        self.assertAlmostEqual(observed["funnel_depth_m"], expected, places=15)
        self.assertAlmostEqual(observed["surface_depth_m"], expected, places=15)
        self.assertAlmostEqual(
            observed["surface_profile_range_m"], float(np.ptp(profile)), places=15
        )

    def test_clip_quality_gate_cannot_be_bypassed_by_zero_mass_repair(self) -> None:
        class ClipOnlyBackend(HighFidelityBackend):
            def advance_one_step(self, *args, **kwargs):
                diagnostics = super().advance_one_step(*args, **kwargs)
                initial_volume = float(kwargs["initial_liquid_volume"])
                return replace(
                    diagnostics,
                    phase_clip_signed_volume_m3=0.0,
                    phase_clip_absolute_volume_m3=2.0e-8 * initial_volume,
                    phase_mass_repair_signed_volume_m3=0.0,
                    phase_mass_repair_absolute_volume_m3=0.0,
                )

        with self.assertRaisesRegex(
            FloatingPointError, "phase clipping numerical quality gate failed"
        ):
            ClipOnlyBackend().run(small_config(target_rpm=0.0))

    def test_zero_rotation_preserves_static_flat_liquid(self) -> None:
        result = HighFidelityBackend().run(small_config(target_rpm=0.0))
        self.assertIs(result.status, RunStatus.COMPLETED)
        maximum_speed = max(
            float(np.max(np.abs(result.fields[name])))
            for name in ("u_r_m_s", "u_z_m_s", "u_theta_m_s")
        )
        self.assertLess(maximum_speed, 1.0e-10)
        self.assertEqual(
            result.metadata["diagnostics"]["surface_tension_model"],
            "projected colour-gradient CSF surrogate; not strict balanced-force",
        )
        self.assertLess(
            result.metadata["diagnostics"]["maximum_projected_divergence_s-1"],
            1.0e-8,
        )
        self.assertLess(float(np.max(result.observables["funnel_depth_m"])), 1.0e-10)
        np.testing.assert_allclose(
            result.fields["phase_liquid_fraction"][0],
            result.fields["phase_liquid_fraction"][-1],
            atol=1.0e-12,
            rtol=0.0,
        )

    def test_short_rotating_run_is_finite_and_has_sparse_fields(self) -> None:
        result = HighFidelityBackend().run(small_config(target_rpm=700.0))
        self.assertIs(result.status, RunStatus.COMPLETED)
        self.assertGreaterEqual(result.time_s.size, 3)
        for array in (*result.observables.values(), *result.fields.values()):
            self.assertTrue(np.isfinite(array).all())
        self.assertEqual(
            result.fields["phase_liquid_fraction"].shape,
            (result.time_s.size, 16, 24),
        )
        self.assertEqual(
            result.observables["surface_profile_m"].shape,
            (result.time_s.size, 16),
        )
        self.assertEqual(result.metadata["fidelity_label"], "HF-preview")
        self.assertEqual(result.metadata["model"], MODEL_NAME)
        self.assertFalse(
            result.metadata["capabilities"]["quantitative_post_contact_air_channel"]
        )
        self.assertFalse(
            result.metadata["capabilities"]["quantitative_absolute_critical_rpm"]
        )
        self.assertEqual(
            result.metadata["diagnostics"]["pressure_factorizations"], 1
        )
        self.assertFalse(result.metadata["diagnostics"]["azimuthal_cells_used"])
        self.assertEqual(
            result.metadata["boundary_conditions"]["top"],
            "impermeable closed lid",
        )
        self.assertEqual(result.metadata["observable_units"]["funnel_depth_m"], "m")
        self.assertEqual(
            result.metadata["observable_units"]["surface_profile_range_m"], "m"
        )
        self.assertIn("observable_definitions", result.metadata)
        np.testing.assert_allclose(
            result.observables["surface_profile_range_m"],
            np.ptp(result.observables["surface_profile_m"], axis=1),
            rtol=0.0,
            atol=2.0e-15,
        )
        completed_steps = result.metadata["diagnostics"]["completed_steps"]
        self.assertEqual(
            result.fields["diagnostic_step_time_s"].size, completed_steps
        )
        self.assertEqual(
            result.fields[
                "diagnostic_pressure_removed_rhs_mean_pa_m2"
            ].size,
            completed_steps,
        )
        self.assertAlmostEqual(
            float(
                np.sum(
                    result.fields[
                        "diagnostic_phase_clip_absolute_volume_m3"
                    ]
                )
            ),
            result.metadata["diagnostics"][
                "cumulative_phase_clip_absolute_volume_m3"
            ],
            places=15,
        )
        self.assertLessEqual(
            result.metadata["diagnostics"]["maximum_rotation_increment_rad"],
            result.metadata["diagnostics"]["rotation_increment_limit_rad"]
            * (1.0 + 1.0e-12),
        )
        self.assertEqual(
            result.metadata["diagnostics"]["pressure_max_iterations"]["status"],
            "reserved_not_applicable_to_direct_splu",
        )
        self.assertTrue(result.metadata["diagnostics"]["quality_gate_pass"])
        self.assertEqual(
            result.metadata["diagnostics"]["numerical_quality_gate"]["kind"],
            "pre_registered_numerical_acceptance_convention",
        )

    def test_pressure_relative_tolerance_is_an_enforced_gate(self) -> None:
        config = small_config(target_rpm=0.0)
        config = replace(
            config,
            high_fidelity=replace(
                config.high_fidelity,
                pressure_relative_tolerance=1.0e-16,
            ),
        )
        with self.assertRaisesRegex(
            FloatingPointError, "pressure projection relative residual"
        ):
            HighFidelityBackend().run(config)

    def test_extreme_forcing_tightens_rotation_and_source_time_step(self) -> None:
        config = small_config(target_rpm=60_000.0)
        config = replace(
            config,
            drive=replace(config.drive, ramp_duration_s=0.0),
        )
        backend = HighFidelityBackend()
        grid = backend.make_grid(config)
        state = backend.initialise_state(config, grid)
        stable_dt, _, _, _, rotation_dt = backend._time_step_limits(
            state, config, grid
        )
        omega = 2.0 * np.pi * config.drive.target_rpm / 60.0
        self.assertLess(stable_dt, config.high_fidelity.time_step_s)
        self.assertLessEqual(omega * rotation_dt, 0.08 * (1.0 + 1.0e-12))
        self.assertLessEqual(omega * stable_dt, 0.08 * (1.0 + 1.0e-12))

    def test_liquid_mass_error_is_bounded(self) -> None:
        result = HighFidelityBackend().run(
            small_config(target_rpm=900.0, duration_s=0.04)
        )
        error = np.max(np.abs(result.observables["liquid_mass_error_relative"]))
        self.assertLess(float(error), 2.0e-12)
        self.assertLess(
            result.metadata["diagnostics"]["maximum_liquid_volume_error"],
            2.0e-12,
        )

    def test_rotation_reversal_leaves_scalar_approximately_invariant(self) -> None:
        positive = HighFidelityBackend().run(
            small_config(target_rpm=800.0, direction=1, duration_s=0.03)
        )
        negative = HighFidelityBackend().run(
            small_config(target_rpm=800.0, direction=-1, duration_s=0.03)
        )
        np.testing.assert_allclose(
            positive.fields["phase_liquid_fraction"],
            negative.fields["phase_liquid_fraction"],
            rtol=2.0e-10,
            atol=2.0e-12,
        )
        np.testing.assert_allclose(
            positive.fields["u_theta_m_s"],
            -negative.fields["u_theta_m_s"],
            rtol=2.0e-10,
            atol=2.0e-12,
        )

    def test_time_step_refinement_has_a_convergent_velocity_trend(self) -> None:
        base = SimulationConfig()
        results = []
        for dt in (1.0e-3, 5.0e-4, 2.5e-4, 1.25e-4):
            config = replace(
                base,
                backend=BackendMode.HIGH_FIDELITY,
                high_fidelity=replace(
                    base.high_fidelity,
                    radial_cells=16,
                    azimuthal_cells=16,
                    axial_cells=24,
                    time_step_s=dt,
                ),
                run=RunConfig(duration_s=0.03, output_interval_s=0.03),
                drive=DriveConfig(
                    initial_rpm=0.0,
                    target_rpm=600.0,
                    ramp_duration_s=0.01,
                    rotation_direction=1,
                ),
            )
            results.append(HighFidelityBackend().run(config))
        reference = results[-1].fields["u_theta_m_s"][-1]
        errors = [
            float(np.linalg.norm(result.fields["u_theta_m_s"][-1] - reference))
            for result in results[:-1]
        ]
        self.assertLess(errors[1], errors[0])
        self.assertLess(errors[2], errors[1])

    def test_backend_has_no_dependency_on_reduced_forward_solver(self) -> None:
        source_path = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "airvortex"
            / "backends"
            / "high_fidelity.py"
        )
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        imported_modules: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported_modules.append(node.module or "")
        self.assertFalse(
            any("forward" in module or "reduced" in module for module in imported_modules),
            imported_modules,
        )


if __name__ == "__main__":
    unittest.main()
