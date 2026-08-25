"""Independent numerical and contract gates for the HF3D local-law backend."""

from __future__ import annotations

import ast
from dataclasses import replace
import json
import math
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np
from scipy.sparse import coo_matrix

import airvortex.backends.high_fidelity_3d as hf3d_module
from airvortex.backends.high_fidelity_3d import (
    HighFidelityBackend,
    _drive_rpm_and_slope,
    _integrated_bar_angle_rad,
    _make_pressure_projection,
)
from airvortex.backends.high_fidelity import HighFidelityBackend as LegacyH1Backend
from airvortex.core import (
    BackendMode,
    DriveConfig,
    RunConfig,
    RunStatus,
    SimulationConfig,
)
from airvortex.numerics import (
    BAR_WALL,
    FLOOR_WALL,
    SIDE_WALL,
    ImmersedCartesianGrid,
    VariableDensityProjection3D,
    advect_bounded_scalar,
    advective_cfl,
    conservative_cls_3d,
    initialise_flat_phase_3d,
    masked_gradient_axis_3d,
    masked_gradient_3d,
    mixture_property_3d,
    newtonian_stress_acceleration_3d,
    rotating_frame_acceleration_3d,
    surface_geometry_3d,
    surface_tension_acceleration_3d,
    topology_diagnostics_3d,
)
from airvortex.runtime import discover_backends


def small_hf3d_config(
    *,
    rpm: float = 0.0,
    duration_s: float = 0.002,
    cells: int = 8,
) -> SimulationConfig:
    base = SimulationConfig()
    return replace(
        base,
        backend=BackendMode.HIGH_FIDELITY,
        drive=DriveConfig(
            initial_rpm=0.0,
            target_rpm=abs(rpm),
            ramp_duration_s=0.001 if rpm else 0.0,
            rotation_direction=1 if rpm >= 0.0 else -1,
        ),
        run=RunConfig(duration_s=duration_s, output_interval_s=0.001),
        forward=replace(base.forward, integration_time_step_s=1.0e-4),
        high_fidelity=replace(
            base.high_fidelity,
            radial_cells=cells,
            azimuthal_cells=cells,
            axial_cells=max(10, 5 * cells // 4),
            time_step_s=1.0e-4,
            pressure_relative_tolerance=1.0e-6,
            pressure_max_iterations=800,
            interface_compression_interval=0,
        ),
    )


def reference_pressure_matrix_and_face_beta(
    grid: ImmersedCartesianGrid,
    density_kg_m3: np.ndarray,
) -> tuple[object, np.ndarray, np.ndarray, np.ndarray]:
    """Pre-optimisation assembly retained as an exact regression oracle."""

    density = np.asarray(density_kg_m3, dtype=np.float64)
    beta_x = np.zeros(grid.face_shape_x, dtype=np.float64)
    beta_y = np.zeros(grid.face_shape_y, dtype=np.float64)
    beta_z = np.zeros(grid.face_shape_z, dtype=np.float64)
    beta_x[1:-1] = 2.0 / (density[:-1] + density[1:])
    beta_y[:, 1:-1] = 2.0 / (density[:, :-1] + density[:, 1:])
    beta_z[:, :, 1:-1] = 2.0 / (
        density[:, :, :-1] + density[:, :, 1:]
    )
    beta_z[:, :, -1] = 1.0 / density[:, :, -1]
    beta_x *= grid.open_x
    beta_y *= grid.open_y
    beta_z *= grid.open_z

    rows: list[np.ndarray] = []
    cols: list[np.ndarray] = []
    values: list[np.ndarray] = []
    diagonal = np.zeros(grid.active_cell_count, dtype=np.float64)

    def add_pairs(
        open_faces: np.ndarray,
        left_index: np.ndarray,
        right_index: np.ndarray,
        coefficient: np.ndarray,
    ) -> None:
        active = np.asarray(open_faces, dtype=bool)
        left = left_index[active]
        right = right_index[active]
        coeff = coefficient[active]
        np.add.at(diagonal, left, coeff)
        np.add.at(diagonal, right, coeff)
        rows.extend((left, right))
        cols.extend((right, left))
        values.extend((-coeff, -coeff))

    add_pairs(
        grid.open_x[1:-1],
        grid.index[:-1],
        grid.index[1:],
        beta_x[1:-1] * (grid.dy * grid.dz / grid.dx),
    )
    add_pairs(
        grid.open_y[:, 1:-1],
        grid.index[:, :-1],
        grid.index[:, 1:],
        beta_y[:, 1:-1] * (grid.dx * grid.dz / grid.dy),
    )
    add_pairs(
        grid.open_z[:, :, 1:-1],
        grid.index[:, :, :-1],
        grid.index[:, :, 1:],
        beta_z[:, :, 1:-1] * (grid.dx * grid.dy / grid.dz),
    )
    top = grid.open_z[:, :, -1]
    top_index = grid.index[:, :, -1][top]
    top_coefficient = beta_z[:, :, -1][top] * (
        grid.dx * grid.dy / (0.5 * grid.dz)
    )
    np.add.at(diagonal, top_index, top_coefficient)
    identity = np.arange(grid.active_cell_count, dtype=np.int64)
    rows.append(identity)
    cols.append(identity)
    values.append(diagonal)
    matrix = coo_matrix(
        (
            np.concatenate(values),
            (np.concatenate(rows), np.concatenate(cols)),
        ),
        shape=(grid.active_cell_count, grid.active_cell_count),
    ).tocsr()
    return matrix, beta_x, beta_y, beta_z


class DriveKinematicsTests(unittest.TestCase):
    def test_piecewise_linear_signed_schedule_and_exact_angle(self) -> None:
        base = SimulationConfig()
        config = replace(
            base,
            run=RunConfig(duration_s=3.0, output_interval_s=0.01),
            drive=DriveConfig(
                actual_speed_schedule_time_s=(0.0, 1.0, 2.0, 3.0),
                actual_speed_schedule_rpm=(0.0, 600.0, -300.0, -300.0),
            ),
        )
        self.assertEqual(_drive_rpm_and_slope(config, 0.5), (300.0, 600.0))
        self.assertEqual(_drive_rpm_and_slope(config, 1.5), (150.0, -900.0))
        self.assertEqual(_drive_rpm_and_slope(config, 2.5), (-300.0, 0.0))
        # Areas in rpm*s: 300 + 150 - 300 = 150, converted to radians.
        self.assertAlmostEqual(
            _integrated_bar_angle_rad(config, 3.0),
            150.0 * 2.0 * math.pi / 60.0,
            places=13,
        )

    def test_single_ramp_direction_and_angle(self) -> None:
        base = SimulationConfig()
        config = replace(
            base,
            run=RunConfig(duration_s=2.0, output_interval_s=0.01),
            drive=DriveConfig(
                initial_rpm=100.0,
                target_rpm=500.0,
                ramp_duration_s=1.0,
                rotation_direction=-1,
            ),
        )
        self.assertEqual(_drive_rpm_and_slope(config, 0.5), (-300.0, -400.0))
        self.assertEqual(_drive_rpm_and_slope(config, 1.5), (-500.0, 0.0))
        expected_rpm_seconds = -0.5 * (100.0 + 500.0) - 500.0
        self.assertAlmostEqual(
            _integrated_bar_angle_rad(config, 2.0),
            expected_rpm_seconds * 2.0 * math.pi / 60.0,
            places=13,
        )


class CartesianGeometryAndInterfaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.grid = ImmersedCartesianGrid(
            16,
            18,
            20,
            0.045,
            0.080,
            bar_half_length_m=0.015,
            bar_radius_m=0.003,
            bar_centre_z_m=0.0045,
        )

    def test_both_transverse_counts_are_real_dimensions_and_bar_volume_is_registered(self) -> None:
        grid = self.grid
        self.assertEqual(grid.shape, (16, 18, 20))
        self.assertEqual(grid.face_shape_x, (17, 18, 20))
        self.assertEqual(grid.face_shape_y, (16, 19, 20))
        self.assertGreater(np.sum(grid.bar_mask), 0)
        self.assertLessEqual(
            abs(grid.rasterized_bar_volume_m3 - grid.exact_bar_volume_m3),
            0.5 * grid.cell_volume_m3 + 1.0e-20,
        )
        self.assertTrue(np.any(grid.open_z[:, :, -1]))
        self.assertFalse(np.any(grid.open_x[[0, -1]]))
        self.assertFalse(np.any(grid.open_y[:, [0, -1]]))

    def test_flat_interface_has_exact_target_volume_and_measurable_width(self) -> None:
        grid = self.grid
        target = math.pi * grid.radius_m**2 * 0.060 - grid.exact_bar_volume_m3
        width = 2.5 * grid.minimum_spacing_m
        phase = initialise_flat_phase_3d(
            grid, 0.060, width, target_liquid_volume_m3=target
        )
        self.assertAlmostEqual(grid.integrate(phase), target, places=14)
        self.assertGreaterEqual(float(np.min(phase[grid.active_mask])), 0.0)
        self.assertLessEqual(float(np.max(phase[grid.active_mask])), 1.0)
        geometry = surface_geometry_3d(
            phase,
            grid,
            liquid_height_m=0.060,
            bar_top_m=0.0075,
            radial_bins=8,
        )
        self.assertLess(geometry.axisymmetry_error, 1.0e-10)
        self.assertLess(geometry.profile_range_m, 1.0e-12)

    def test_closed_phase_transport_and_cls_expose_exact_mass_budget(self) -> None:
        grid = self.grid
        target = math.pi * grid.radius_m**2 * 0.060 - grid.exact_bar_volume_m3
        width = 2.5 * grid.minimum_spacing_m
        phase = initialise_flat_phase_3d(
            grid, 0.060, width, target_liquid_volume_m3=target
        )
        zeros_x = np.zeros(grid.face_shape_x)
        zeros_y = np.zeros(grid.face_shape_y)
        zeros_z = np.zeros(grid.face_shape_z)
        transported, diagnostics = advect_bounded_scalar(
            phase, zeros_x, zeros_y, zeros_z, grid, 0.01
        )
        self.assertAlmostEqual(diagnostics.old_integral, target, places=14)
        self.assertAlmostEqual(diagnostics.final_integral, target, places=14)
        np.testing.assert_allclose(transported, phase, rtol=0.0, atol=1.0e-15)
        compressed, cls = conservative_cls_3d(
            transported, grid, width, pseudo_cfl=0.04, iterations=3
        )
        self.assertAlmostEqual(grid.integrate(compressed), target, places=14)
        self.assertAlmostEqual(cls.final_volume_m3, target, places=14)

    def test_three_dimensional_topology_needs_physical_throat_gate(self) -> None:
        grid = self.grid
        phase = np.ones(grid.shape, dtype=np.float64)
        phase[:, :, -2:] = 0.0
        narrow = (np.abs(grid.x_cell) < 0.5 * grid.dx) & (
            np.abs(grid.y_cell) < 0.5 * grid.dy
        )
        phase[narrow & (grid.z_cell >= grid.bar_centre_z_m)] = 0.0
        one_column = topology_diagnostics_3d(
            phase,
            grid,
            interface_thickness_m=2.0 * grid.minimum_spacing_m,
            minimum_resolved_throat_cells=4,
        )
        self.assertFalse(one_column.resolved_channel)

        wide = (grid.x_cell**2 + grid.y_cell**2) < (0.020**2)
        phase[wide & (grid.z_cell >= grid.bar_centre_z_m)] = 0.0
        resolved = topology_diagnostics_3d(
            phase,
            grid,
            interface_thickness_m=2.0 * grid.minimum_spacing_m,
            minimum_resolved_throat_cells=2,
        )
        self.assertTrue(resolved.touches_bar)
        self.assertTrue(resolved.finite_channel_candidate)
        self.assertTrue(resolved.resolved_channel)
        self.assertGreater(resolved.bar_gas_contact_area_m2, 0.0)


class LocalLawOperatorTests(unittest.TestCase):
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

    def test_variable_density_projection_reduces_divergence(self) -> None:
        grid = self.grid
        rng = np.random.default_rng(20270821)
        ux = rng.normal(scale=0.02, size=grid.face_shape_x) * grid.open_x
        uy = rng.normal(scale=0.02, size=grid.face_shape_y) * grid.open_y
        uz = rng.normal(scale=0.02, size=grid.face_shape_z) * grid.open_z
        phase = np.where(grid.z_cell < 0.060, 1.0, 0.0)
        density = mixture_property_3d(phase, 998.2, 1.204)
        projection = VariableDensityProjection3D(
            grid, relative_tolerance=1.0e-9, maximum_iterations=1200
        )
        _, _, _, pressure, diagnostics = projection.project(
            ux, uy, uz, density, 1.0e-4
        )
        self.assertGreater(diagnostics.divergence_before_s_inv, 1.0)
        self.assertLess(
            diagnostics.divergence_after_s_inv,
            2.0e-8 * diagnostics.divergence_before_s_inv,
        )
        self.assertLess(diagnostics.pressure_relative_residual, 1.1e-9)
        self.assertTrue(np.isfinite(pressure).all())

    def test_directional_gradient_is_bitwise_the_requested_full_component(self) -> None:
        rng = np.random.default_rng(20260825)
        field = rng.normal(size=self.grid.shape)
        for top_dirichlet in (None, 0.0, 0.37):
            full = masked_gradient_3d(
                field,
                self.grid,
                top_dirichlet=top_dirichlet,
            )
            for axis in range(3):
                directional = masked_gradient_axis_3d(
                    field,
                    self.grid,
                    axis,
                    top_dirichlet=top_dirichlet,
                )
                np.testing.assert_array_equal(directional, full[axis])

        for invalid in (-1, 3, True, 1.5):
            with self.assertRaises(ValueError):
                masked_gradient_axis_3d(field, self.grid, invalid)  # type: ignore[arg-type]

    def test_cached_pressure_assembly_is_exactly_the_reference_operator(self) -> None:
        grid = self.grid
        rng = np.random.default_rng(20260822)
        density = rng.uniform(1.204, 998.2, size=grid.shape)
        projection = VariableDensityProjection3D(
            grid, relative_tolerance=1.0e-9, maximum_iterations=1200
        )

        expected = reference_pressure_matrix_and_face_beta(grid, density)
        observed = projection._matrix_and_face_beta(density)
        cached_matrix = observed[0]
        cached_face_beta = observed[1:]
        np.testing.assert_array_equal(observed[0].indptr, expected[0].indptr)
        np.testing.assert_array_equal(observed[0].indices, expected[0].indices)
        np.testing.assert_array_equal(observed[0].data, expected[0].data)
        for actual, reference in zip(observed[1:], expected[1:]):
            np.testing.assert_array_equal(actual, reference)

        # Coefficients change with density, while the same canonical CSR object
        # and all density-independent connectivity are retained.
        next_density = density[::-1, ::-1, ::-1].copy()
        expected_next = reference_pressure_matrix_and_face_beta(grid, next_density)
        observed_next = projection._matrix_and_face_beta(next_density)
        self.assertIs(observed_next[0], cached_matrix)
        for actual, cached in zip(observed_next[1:], cached_face_beta):
            self.assertIs(actual, cached)
        np.testing.assert_array_equal(observed_next[0].indptr, expected_next[0].indptr)
        np.testing.assert_array_equal(observed_next[0].indices, expected_next[0].indices)
        np.testing.assert_array_equal(observed_next[0].data, expected_next[0].data)
        for actual, reference in zip(observed_next[1:], expected_next[1:]):
            np.testing.assert_array_equal(actual, reference)

    def test_pressure_history_guess_minimises_residual_and_resets_on_discontinuity(
        self,
    ) -> None:
        grid = self.grid
        rng = np.random.default_rng(20260823)
        ux = rng.normal(scale=0.02, size=grid.face_shape_x) * grid.open_x
        uy = rng.normal(scale=0.02, size=grid.face_shape_y) * grid.open_y
        uz = rng.normal(scale=0.02, size=grid.face_shape_z) * grid.open_z
        phase = np.where(grid.z_cell < 0.060, 1.0, 0.0)
        density = mixture_property_3d(phase, 998.2, 1.204)
        projection = VariableDensityProjection3D(
            grid, relative_tolerance=1.0e-10, maximum_iterations=1600
        )

        pressure = None
        last_diagnostics = None
        for scale in (1.0, 1.1, 1.2):
            _, _, _, pressure, last_diagnostics = projection.project(
                scale * ux,
                scale * uy,
                scale * uz,
                density,
                1.0e-4,
                initial_pressure_pa=pressure,
            )
        self.assertEqual(projection.initial_guess_extrapolation_count, 1)
        self.assertGreater(projection.last_initial_guess_extrapolation_factor, 0.9)
        self.assertLess(projection.last_initial_guess_extrapolation_factor, 1.1)
        self.assertLess(last_diagnostics.pressure_relative_residual, 1.1e-10)

        # An explicitly unrelated pressure field breaks the history chain.
        zeros = np.zeros(grid.shape, dtype=np.float64)
        _, _, _, pressure, _ = projection.project(
            ux,
            uy,
            uz,
            density,
            1.0e-4,
            initial_pressure_pa=zeros,
        )
        self.assertEqual(projection.initial_guess_extrapolation_count, 1)
        self.assertEqual(projection.last_initial_guess_extrapolation_factor, 0.0)
        projection.project(
            1.1 * ux,
            1.1 * uy,
            1.1 * uz,
            density,
            1.0e-4,
            initial_pressure_pa=pressure,
        )
        self.assertEqual(projection.initial_guess_extrapolation_count, 1)

    def test_pressure_history_checkpoint_restores_rejected_trial_in_constant_time(
        self,
    ) -> None:
        grid = self.grid
        rng = np.random.default_rng(20260826)
        ux = rng.normal(scale=0.02, size=grid.face_shape_x) * grid.open_x
        uy = rng.normal(scale=0.02, size=grid.face_shape_y) * grid.open_y
        uz = rng.normal(scale=0.02, size=grid.face_shape_z) * grid.open_z
        density = np.where(grid.z_cell < 0.060, 998.2, 1.204)
        projection = VariableDensityProjection3D(
            grid, relative_tolerance=1.0e-10, maximum_iterations=1600
        )
        reference = VariableDensityProjection3D(
            grid, relative_tolerance=1.0e-10, maximum_iterations=1600
        )

        pressure = None
        reference_pressure = None
        for scale in (1.0, 1.1):
            *_, pressure, _ = projection.project(
                scale * ux,
                scale * uy,
                scale * uz,
                density,
                1.0e-4,
                initial_pressure_pa=pressure,
            )
            *_, reference_pressure, _ = reference.project(
                scale * ux,
                scale * uy,
                scale * uz,
                density,
                1.0e-4,
                initial_pressure_pa=reference_pressure,
            )

        checkpoint = projection.checkpoint_history()
        previous_identity = projection._previous_pressure_solution
        latest_identity = projection._last_pressure_solution
        projection.project(
            1.2 * ux,
            1.2 * uy,
            1.2 * uz,
            density,
            8.0e-5,
            initial_pressure_pa=pressure,
        )
        self.assertGreater(projection.solve_count, checkpoint.solve_count)
        projection.restore_history(checkpoint)
        self.assertEqual(projection.solve_count, checkpoint.solve_count)
        self.assertEqual(
            projection.initial_guess_extrapolation_count,
            checkpoint.initial_guess_extrapolation_count,
        )
        self.assertIs(projection._previous_pressure_solution, previous_identity)
        self.assertIs(projection._last_pressure_solution, latest_identity)

        observed = projection.project(
            1.2 * ux,
            1.2 * uy,
            1.2 * uz,
            density,
            8.0e-5,
            initial_pressure_pa=pressure,
        )
        expected = reference.project(
            1.2 * ux,
            1.2 * uy,
            1.2 * uz,
            density,
            8.0e-5,
            initial_pressure_pa=reference_pressure,
        )
        for actual, wanted in zip(observed[:4], expected[:4]):
            np.testing.assert_array_equal(actual, wanted)

        other = VariableDensityProjection3D(
            grid, relative_tolerance=1.0e-10, maximum_iterations=1600
        )
        with self.assertRaisesRegex(ValueError, "another solver"):
            other.restore_history(checkpoint)

    def test_pressure_preconditioner_falls_back_when_vertical_lines_are_coarse(
        self,
    ) -> None:
        coarse_vertical_grid = ImmersedCartesianGrid(
            24,
            24,
            10,
            0.045,
            0.080,
            bar_half_length_m=0.015,
            bar_radius_m=0.003,
            bar_centre_z_m=0.0045,
        )
        coarse_projection = VariableDensityProjection3D(
            coarse_vertical_grid,
            relative_tolerance=1.0e-9,
            maximum_iterations=1200,
        )
        fine_projection = VariableDensityProjection3D(
            self.grid,
            relative_tolerance=1.0e-9,
            maximum_iterations=1200,
        )
        self.assertEqual(coarse_projection.preconditioner_name, "point-Jacobi")
        self.assertEqual(
            fine_projection.preconditioner_name,
            "vertical-line block-Jacobi",
        )
        rng = np.random.default_rng(20260824)
        ux = rng.normal(size=coarse_vertical_grid.face_shape_x)
        uy = rng.normal(size=coarse_vertical_grid.face_shape_y)
        uz = rng.normal(size=coarse_vertical_grid.face_shape_z)
        density = np.where(
            coarse_vertical_grid.z_cell < 0.060,
            998.2,
            1.204,
        )
        *_, diagnostics = coarse_projection.project(
            ux,
            uy,
            uz,
            density,
            1.0e-4,
        )
        self.assertLess(diagnostics.pressure_relative_residual, 1.1e-9)

    def test_rotating_frame_acceleration_has_all_three_noninertial_terms(self) -> None:
        grid = self.grid
        ux = np.full(grid.shape, 0.3)
        uy = np.full(grid.shape, -0.2)
        omega = 5.0
        omega_dot = -2.0
        ax, ay, az = rotating_frame_acceleration_3d(
            ux, uy, grid, omega, omega_dot
        )
        point = tuple(np.argwhere(grid.active_mask)[-1])
        i, j, k = point
        self.assertAlmostEqual(
            ax[i, j, k],
            2.0 * omega * uy[i, j, k]
            + omega**2 * grid.x_cell[i, j, k]
            + omega_dot * grid.y_cell[i, j, k],
        )
        self.assertAlmostEqual(
            ay[i, j, k],
            -2.0 * omega * ux[i, j, k]
            + omega**2 * grid.y_cell[i, j, k]
            - omega_dot * grid.x_cell[i, j, k],
        )
        self.assertEqual(az[i, j, k], 0.0)

    def test_zero_velocity_has_zero_newtonian_stress_and_finite_csf(self) -> None:
        grid = self.grid
        target = math.pi * grid.radius_m**2 * 0.060 - grid.exact_bar_volume_m3
        phase = initialise_flat_phase_3d(
            grid,
            0.060,
            2.5 * grid.minimum_spacing_m,
            target_liquid_volume_m3=target,
        )
        density = mixture_property_3d(phase, 998.2, 1.204)
        viscosity = mixture_property_3d(phase, 1.002e-3, 1.825e-5)
        zeros = np.zeros(grid.shape)
        ax, ay, az, diagnostics = newtonian_stress_acceleration_3d(
            zeros, zeros, zeros, viscosity, density, grid
        )
        self.assertEqual(diagnostics.viscous_dissipation_w, 0.0)
        self.assertEqual(diagnostics.maximum_viscous_acceleration_m_s2, 0.0)
        self.assertFalse(np.any(ax) or np.any(ay) or np.any(az))
        *forces, csf = surface_tension_acceleration_3d(
            phase,
            density,
            grid,
            0.072,
            contact_angles_deg={SIDE_WALL: 90.0, FLOOR_WALL: 90.0, BAR_WALL: 85.0},
        )
        self.assertTrue(all(np.isfinite(value).all() for value in forces))
        self.assertGreater(csf.interface_band_cells, 0)


class HF3DBackendContractTests(unittest.TestCase):
    def test_registry_selects_hf3d_while_legacy_h1_remains_directly_available(self) -> None:
        backend = discover_backends(probe=True).create("high_fidelity")
        self.assertIsInstance(backend, HighFidelityBackend)
        self.assertTrue(backend.capabilities["three_dimensional"])
        self.assertIsInstance(LegacyH1Backend(), LegacyH1Backend)

    def test_explicit_cpu_pressure_selection_is_recorded_without_cuda_probe(self) -> None:
        config = small_hf3d_config()
        config = replace(
            config,
            high_fidelity=replace(
                config.high_fidelity, pressure_solver_device="cpu"
            ),
        )
        grid = HighFidelityBackend().make_grid(config)
        projection, record = _make_pressure_projection(config, grid)
        self.assertIsInstance(projection, VariableDensityProjection3D)
        self.assertEqual(record["requested"], "cpu")
        self.assertEqual(record["selected"], "cpu")
        self.assertFalse(record["fallback"])
        self.assertFalse(record["host_device_transfer_each_solve"])

    def test_combined_known_source_bound_targets_margin_below_hard_gate(self) -> None:
        config = small_hf3d_config(rpm=100.0)
        backend = HighFidelityBackend()
        grid = backend.make_grid(config)
        state = backend.initialise_state(config, grid)
        stable_dt, limits = backend._time_step_limits(state, config, grid)
        combined_dt = float(limits["combined_known_source_cfl_dt_s"])
        rate = float(limits["advective_rate_s_inv"])
        acceleration = float(
            limits["known_source_rate_acceleration_s_inv2"]
        )
        target = float(limits["cfl_controller_target"])
        self.assertLess(target, config.high_fidelity.cfl_limit)
        self.assertLessEqual(
            rate * combined_dt + acceleration * combined_dt**2,
            target * (1.0 + 1.0e-14),
        )
        self.assertLessEqual(stable_dt, combined_dt)

    def test_synthetic_post_projection_cfl_failure_rolls_back_and_retries(self) -> None:
        config = small_hf3d_config(rpm=100.0)
        config = replace(
            config,
            high_fidelity=replace(
                config.high_fidelity, pressure_solver_device="cpu"
            ),
        )
        call_count = 0

        def inject_one_rejected_post_projection_cfl(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            observed = advective_cfl(*args, **kwargs)
            if call_count == 4:
                return config.high_fidelity.cfl_limit * 1.001
            return observed

        with patch.object(
            hf3d_module,
            "advective_cfl",
            side_effect=inject_one_rejected_post_projection_cfl,
        ):
            result = HighFidelityBackend().run(config)

        diagnostics = result.metadata["diagnostics"]
        self.assertEqual(diagnostics["cfl_retry_attempts_total"], 1)
        self.assertEqual(diagnostics["cfl_retried_accepted_steps"], 1)
        self.assertEqual(
            diagnostics["pressure_solves"], diagnostics["completed_steps"]
        )
        self.assertEqual(
            int(result.fields["diagnostic_cfl_retry_count"][0]), 1
        )
        self.assertLess(
            result.fields["diagnostic_dt_s"][0],
            result.fields["diagnostic_initial_attempt_dt_s"][0],
        )
        retry_code = diagnostics["time_step_limiter_codes"][
            "post_projection_cfl_retry"
        ]
        self.assertEqual(
            int(result.fields["diagnostic_time_step_limiter_code"][0]),
            retry_code,
        )
        retry_events = [
            event for event in result.events if event.kind == "cfl_step_retry"
        ]
        self.assertEqual(len(retry_events), 1)
        self.assertEqual(
            retry_events[0].data["pressure_accelerator_selected"], "cpu"
        )

    def test_rejected_trial_leaves_flow_state_and_pressure_history_unchanged(
        self,
    ) -> None:
        config = small_hf3d_config(rpm=100.0)
        config = replace(
            config,
            high_fidelity=replace(
                config.high_fidelity, pressure_solver_device="cpu"
            ),
        )
        backend = HighFidelityBackend()
        grid = backend.make_grid(config)
        state = backend.initialise_state(config, grid)
        projection, _ = _make_pressure_projection(config, grid)
        pressure_checkpoint = projection.checkpoint_history()
        scalar_state = (state.time_s, state.step, state.bar_angle_rad)
        array_state = tuple(
            value.copy()
            for value in (
                state.u_x_face,
                state.u_y_face,
                state.u_z_face,
                state.phase,
                state.pressure_pa,
            )
        )
        call_count = 0

        def inject_rejected_post_projection_cfl(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            observed = advective_cfl(*args, **kwargs)
            if call_count == 4:
                return config.high_fidelity.cfl_limit * 1.001
            return observed

        with patch.object(
            hf3d_module,
            "advective_cfl",
            side_effect=inject_rejected_post_projection_cfl,
        ):
            with self.assertRaises(hf3d_module.HF3DPostProjectionCFLExceeded):
                backend.advance_one_step(
                    state,
                    config,
                    grid,
                    projection,
                    config.high_fidelity.time_step_s,
                    expected_liquid_volume_m3=backend._initial_liquid_volume(
                        config, grid
                    ),
                )

        self.assertEqual(
            (state.time_s, state.step, state.bar_angle_rad), scalar_state
        )
        for actual, expected in zip(
            (
                state.u_x_face,
                state.u_y_face,
                state.u_z_face,
                state.phase,
                state.pressure_pa,
            ),
            array_state,
        ):
            np.testing.assert_array_equal(actual, expected)
        self.assertEqual(projection.solve_count, pressure_checkpoint.solve_count)
        self.assertEqual(
            projection.initial_guess_extrapolation_count,
            pressure_checkpoint.initial_guess_extrapolation_count,
        )
        self.assertIs(
            projection._previous_pressure_solution,
            pressure_checkpoint.previous_pressure_solution,
        )
        self.assertIs(
            projection._last_pressure_solution,
            pressure_checkpoint.last_pressure_solution,
        )

    def test_exhausted_retry_error_is_machine_readable_and_names_accelerator(
        self,
    ) -> None:
        config = small_hf3d_config(rpm=100.0)
        config = replace(
            config,
            high_fidelity=replace(
                config.high_fidelity,
                pressure_solver_device="cpu",
                maximum_cfl_step_retries=0,
            ),
        )
        call_count = 0

        def inject_rejected_post_projection_cfl(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            observed = advective_cfl(*args, **kwargs)
            if call_count == 4:
                return config.high_fidelity.cfl_limit * 1.001
            return observed

        with patch.object(
            hf3d_module,
            "advective_cfl",
            side_effect=inject_rejected_post_projection_cfl,
        ):
            with self.assertRaisesRegex(
                FloatingPointError, "HF3D CFL retry gate exhausted"
            ) as raised:
                HighFidelityBackend().run(config)

        _, payload_text = str(raised.exception).split(": ", 1)
        payload = json.loads(payload_text)
        self.assertEqual(payload["kind"], "HF3D_CFL_RETRY_EXHAUSTED")
        self.assertEqual(payload["retry_count"], 0)
        self.assertEqual(payload["pressure_accelerator"]["selected"], "cpu")
        rejected = payload["last_rejected_trial"]
        for required in (
            "starting_advective_cfl",
            "pre_source_advective_cfl",
            "pre_projection_advective_cfl",
            "post_projection_advective_cfl",
            "maximum_momentum_advection_acceleration_m_s2",
            "maximum_viscous_acceleration_m_s2",
            "maximum_boundary_acceleration_m_s2",
            "maximum_surface_tension_acceleration_m_s2",
            "maximum_rotating_frame_acceleration_m_s2",
            "gravity_acceleration_m_s2",
            "maximum_pressure_projection_acceleration_m_s2",
        ):
            self.assertIn(required, rejected)

    def test_backend_smoke_archives_full_final_3d_and_gui_averages(self) -> None:
        config = small_hf3d_config(rpm=100.0)
        progress_messages = []
        result = HighFidelityBackend().run(
            config, progress=progress_messages.append
        )
        self.assertIs(result.status, RunStatus.COMPLETED)
        self.assertEqual(result.metadata["backend_id"], "high_fidelity_3d_local_law")
        self.assertTrue(result.metadata["capabilities"]["three_dimensional"])
        self.assertTrue(result.metadata["capabilities"]["azimuthal_cells_used"])
        self.assertFalse(
            result.metadata["capabilities"]["imports_forward_theory_closures"]
        )
        self.assertFalse(
            result.metadata["capabilities"]["quantitative_absolute_critical_rpm"]
        )
        self.assertEqual(
            result.metadata["diagnostics"]["numerical_quality_gate"][
                "physical_prediction_status"
            ],
            "NO-GO_UNCALIBRATED_UNCONVERGED",
        )
        shape = (
            config.high_fidelity.radial_cells,
            config.high_fidelity.azimuthal_cells,
            config.high_fidelity.axial_cells,
        )
        self.assertEqual(
            result.fields["final_phase_liquid_fraction_3d"].shape, shape
        )
        self.assertEqual(result.fields["active_fluid_mask_3d"].shape, shape)
        self.assertEqual(
            result.fields["phase_liquid_fraction"].shape[0], result.time_s.size
        )
        self.assertEqual(
            result.observables["surface_profile_m"].shape[0], result.time_s.size
        )
        self.assertLess(
            abs(
                result.metadata["diagnostics"][
                    "liquid_open_boundary_balance_error_relative"
                ]
            ),
            1.0e-10,
        )
        self.assertTrue(all(np.isfinite(value).all() for value in result.fields.values()))
        surface_progress = [
            item
            for item in progress_messages
            if "surface_profile_m" in item.metrics
        ]
        progress_times = np.asarray(
            [item.simulated_time_s for item in surface_progress], dtype=float
        )
        for official_time in result.time_s:
            self.assertTrue(np.any(np.isclose(progress_times, official_time)))
        for item in surface_progress:
            radius = item.metrics["surface_radius_m"]
            mean = item.metrics["surface_profile_m"]
            minimum = item.metrics["surface_profile_minimum_m"]
            maximum = item.metrics["surface_profile_maximum_m"]
            self.assertEqual(len(radius), len(mean))
            self.assertEqual(len(radius), len(minimum))
            self.assertEqual(len(radius), len(maximum))
            self.assertTrue(
                all(low <= centre <= high for low, centre, high in zip(minimum, mean, maximum))
            )

    def test_live_surface_preview_uses_completed_steps_without_archiving_extra_frames(self) -> None:
        config = small_hf3d_config(rpm=100.0)
        progress_messages = []
        wall_clock = iter(float(index * 2) for index in range(10_000))
        with patch(
            "airvortex.backends.high_fidelity_3d.time.monotonic",
            side_effect=lambda: next(wall_clock),
        ):
            result = HighFidelityBackend().run(
                config, progress=progress_messages.append
            )

        preview_times = np.asarray(
            [
                item.simulated_time_s
                for item in progress_messages
                if "surface_profile_m" in item.metrics
            ],
            dtype=float,
        )
        self.assertGreater(preview_times.size, result.time_s.size)
        self.assertTrue(
            any(
                not np.any(np.isclose(value, result.time_s))
                for value in preview_times
            )
        )
        self.assertEqual(
            result.observables["surface_profile_m"].shape[0],
            result.time_s.size,
        )

    def test_zero_drive_is_discretely_hydrostatic_not_a_wall_relaxation_current(self) -> None:
        result = HighFidelityBackend().run(small_hf3d_config(rpm=0.0))
        speed = np.sqrt(
            result.fields["final_u_x_lab_m_s_3d"] ** 2
            + result.fields["final_u_y_lab_m_s_3d"] ** 2
            + result.fields["final_u_z_lab_m_s_3d"] ** 2
        )
        self.assertLess(float(np.max(speed)), 2.0e-6)
        self.assertLess(float(np.max(result.observables["funnel_depth_m"])), 1.0e-8)

    def test_hf3d_source_does_not_import_forward_backend_or_closures(self) -> None:
        source_path = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "airvortex"
            / "backends"
            / "high_fidelity_3d.py"
        )
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.append(node.module or "")
        self.assertFalse(any("forward" in name for name in imported), imported)
        source = source_path.read_text(encoding="utf-8")
        for forbidden in (
            "circulation_coupling_m2",
            "interface_mobility_m_pa_s",
            "effective_target_radius_m",
            "axial_swirl_decay_length_m",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
