from __future__ import annotations

from dataclasses import replace
import math
from pathlib import Path
from types import SimpleNamespace
import sys
import time
import unittest
from unittest.mock import patch

import numpy as np
from scipy.integrate import solve_bvp


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from airvortex import SimulationConfig  # noqa: E402
from airvortex.backends import (  # noqa: E402
    NonlinearSurfaceError,
    NonlinearSurfaceSolver,
    UnderresolvedSurfaceError,
    burgers_dimensionless_potential,
    centrifugal_potential_per_circulation_sq,
    solve_first_contact,
    solve_unit_surface_response,
    stir_bar_top_height_m,
)


def configured_solver(
    *,
    points: int = 128,
    relative_tolerance: float = 1.0e-7,
    absolute_tolerance: float = 1.0e-9,
    sigma: float = 0.072,
) -> tuple[SimulationConfig, object, NonlinearSurfaceSolver]:
    base = SimulationConfig()
    config = replace(
        base,
        fluid=replace(base.fluid, surface_tension_n_m=sigma),
        forward=replace(
            base.forward,
            surface_radial_points=points,
            relative_tolerance=relative_tolerance,
            absolute_tolerance=absolute_tolerance,
        ),
    )
    linear = solve_unit_surface_response(config)
    solver = NonlinearSurfaceSolver(
        config,
        linear_radius_m=linear.radius_m,
        linear_surface_per_circulation_sq=linear.surface_per_circulation_sq,
    )
    return config, linear, solver


class ExactCurvatureSurfaceTests(unittest.TestCase):
    def test_dimensionless_burgers_potential_matches_independent_quadrature(self) -> None:
        config, _, solver = configured_solver(points=256)
        dimensionless_radius = np.linspace(0.0, solver.radius_ratio, 256)
        radius = dimensionless_radius * config.forward.effective_core_radius_m
        dimensional = centrifugal_potential_per_circulation_sq(
            radius, config.forward.effective_core_radius_m
        )
        expected = (
            config.forward.effective_core_radius_m**2 * dimensional
        )
        np.testing.assert_allclose(
            burgers_dimensionless_potential(dimensionless_radius),
            expected,
            rtol=2.0e-11,
            atol=2.0e-14,
        )
        self.assertEqual(float(burgers_dimensionless_potential(0.0)), 0.0)
        self.assertAlmostEqual(
            float(burgers_dimensionless_potential(1.0e6)),
            math.log(2.0),
            places=10,
        )

    def test_small_forcing_converges_to_linear_curvature_baseline(self) -> None:
        _, linear, solver = configured_solver(points=256)
        forcing = 1.0e-3
        nonlinear = solver.solve_forcing(forcing)
        circulation_sq = solver.circulation_sq_from_forcing(forcing)
        linear_elevation = (
            circulation_sq * linear.surface_per_circulation_sq
        )
        relative_error = float(
            np.max(np.abs(nonlinear.elevation_m - linear_elevation))
            / np.max(np.abs(linear_elevation))
        )
        self.assertLess(relative_error, 3.0e-5)
        self.assertEqual(
            nonlinear.solution_regime, "EXACT_NONLINEAR_CURVATURE"
        )

    def test_sigma_to_zero_matches_finite_vessel_outer_solution(self) -> None:
        config, _, solver = configured_solver(
            points=256,
            relative_tolerance=1.0e-7,
            absolute_tolerance=1.0e-10,
            sigma=1.0e-10,
        )
        forcing = 2.0e9
        solution = solver.solve_capillary_singular_limit(forcing)
        self.assertEqual(
            solution.solution_regime, "CAPILLARY_SINGULAR_LIMIT_OUTER"
        )
        circulation_sq = solver.circulation_sq_from_forcing(forcing)
        potential = centrifugal_potential_per_circulation_sq(
            solution.radius_m, config.forward.effective_core_radius_m
        )
        expected = circulation_sq / config.fluid.gravity_m_s2 * (
            potential - float(np.dot(solver.volume_weights, potential))
        )
        np.testing.assert_allclose(
            solution.elevation_m, expected, rtol=2.0e-11, atol=2.0e-13
        )
        self.assertLess(abs(solution.volume_residual_m3), 1.0e-18)

    def test_underresolved_positive_sigma_fails_closed_not_to_outer_limit(self) -> None:
        _, _, solver = configured_solver(
            points=64,
            relative_tolerance=1.0e-6,
            absolute_tolerance=1.0e-9,
            sigma=1.0e-10,
        )
        self.assertFalse(solver.passes_capillary_resolution_floor)
        with self.assertRaisesRegex(
            UnderresolvedSurfaceError,
            "finite-sigma.*underresolved.*increase.*surface_radial_points",
        ):
            solver.solve_forcing(1.0)
        explicit = solver.solve_capillary_singular_limit(1.0)
        self.assertEqual(
            explicit.solution_regime, "CAPILLARY_SINGULAR_LIMIT_OUTER"
        )

    def test_resolved_finite_sigma_sequence_approaches_explicit_outer_limit(self) -> None:
        base = SimulationConfig()
        circulation = (
            base.forward.circulation_coupling_m2
            * 100.0
            * 2.0
            * math.pi
            / 60.0
        )
        circulation_sq = circulation**2
        centre_errors: list[float] = []
        for sigma, points in ((0.020, 128), (0.005, 256), (0.001, 640)):
            config = replace(
                base,
                fluid=replace(base.fluid, surface_tension_n_m=sigma),
                forward=replace(
                    base.forward,
                    surface_radial_points=points,
                    relative_tolerance=1.0e-6,
                    absolute_tolerance=1.0e-9,
                ),
            )
            linear = solve_unit_surface_response(config)
            solver = NonlinearSurfaceSolver(
                config,
                linear_radius_m=linear.radius_m,
                linear_surface_per_circulation_sq=(
                    linear.surface_per_circulation_sq
                ),
            )
            self.assertTrue(solver.passes_capillary_resolution_floor)
            finite_sigma = solver.solve(circulation_sq)
            outer = solver.solve_capillary_singular_limit(
                solver.forcing_from_circulation_sq(circulation_sq)
            )
            self.assertEqual(
                finite_sigma.solution_regime, "EXACT_NONLINEAR_CURVATURE"
            )
            self.assertLess(
                abs(float(finite_sigma.normalised_slope[-1])), 2.0e-8
            )
            centre_errors.append(
                abs(
                    float(finite_sigma.elevation_m[0])
                    - float(outer.elevation_m[0])
                )
            )
        self.assertLess(centre_errors[1], 0.35 * centre_errors[0])
        self.assertLess(centre_errors[2], 0.25 * centre_errors[1])

    def test_exact_volume_neumann_boundaries_and_residual_certificate(self) -> None:
        _, _, solver = configured_solver(points=256, relative_tolerance=1.0e-8)
        solution = solver.solve_forcing(100.0)
        self.assertLess(abs(solution.volume_residual_m3), 1.0e-18)
        self.assertEqual(float(solution.normalised_slope[0]), 0.0)
        self.assertLess(abs(float(solution.normalised_slope[-1])), 2.0e-9)
        self.assertLessEqual(
            solution.maximum_collocation_rms_residual,
            1.25 * solver.collocation_tolerance,
        )
        self.assertLessEqual(
            solution.boundary_residual_norm,
            5.0 * solver.collocation_tolerance,
        )
        self.assertTrue(np.isfinite(solution.curvature_m_inv).all())

    def test_radial_grid_convergence_is_second_order_at_the_axis(self) -> None:
        centres: list[float] = []
        for points in (128, 256, 512):
            _, _, solver = configured_solver(
                points=points,
                relative_tolerance=1.0e-7,
                absolute_tolerance=1.0e-9,
            )
            centres.append(float(solver.solve_forcing(100.0).elevation_m[0]))
        coarse_change = abs(centres[1] - centres[0])
        fine_change = abs(centres[2] - centres[1])
        self.assertLess(fine_change, 0.30 * coarse_change)
        self.assertLess(fine_change, 4.0e-7)

    def test_tolerance_refinement_reduces_certified_collocation_residual(self) -> None:
        residuals: list[float] = []
        centres: list[float] = []
        for tolerance in (1.0e-4, 1.0e-6, 1.0e-8):
            _, _, solver = configured_solver(
                points=128,
                relative_tolerance=tolerance,
                absolute_tolerance=tolerance * 1.0e-2,
            )
            solution = solver.solve_forcing(100.0)
            residuals.append(solution.maximum_collocation_rms_residual)
            centres.append(float(solution.elevation_m[0]))
        self.assertGreater(residuals[0], 50.0 * residuals[1])
        self.assertGreater(residuals[1], 50.0 * residuals[2])
        self.assertLess(abs(centres[2] - centres[1]), 2.0e-9)

    def test_default_first_contact_is_a_model_regression_not_validation(self) -> None:
        config, linear, solver = configured_solver(
            points=256,
            relative_tolerance=1.0e-8,
            absolute_tolerance=1.0e-10,
        )
        gap = config.container.liquid_depth_m - stir_bar_top_height_m(config)
        linear_critical_circulation_sq = gap / -float(
            np.min(linear.surface_per_circulation_sq)
        )
        result = solve_first_contact(
            solver,
            static_gap_m=gap,
            circulation_coupling_m2=config.forward.circulation_coupling_m2,
            linear_critical_circulation_sq_m4_s2=(
                linear_critical_circulation_sq
            ),
        )
        # Frozen numerical regression for the declared core defaults, not an
        # experimental acceptance interval.
        self.assertAlmostEqual(result.dimensionless_forcing, 131.6393, delta=8.0e-4)
        self.assertAlmostEqual(result.actual_rpm, 1033.9014, delta=0.005)
        self.assertLess(abs(result.height_residual_m), 2.0e-9)
        self.assertAlmostEqual(
            result.surface.maximum_absolute_normalised_slope,
            0.9716273,
            delta=2.0e-5,
        )

    def test_direct_w_state_parameter_bvp_matches_continuation_root(self) -> None:
        config, linear, solver = configured_solver(
            points=256,
            relative_tolerance=1.0e-7,
            absolute_tolerance=1.0e-9,
        )
        gap = config.container.liquid_depth_m - stir_bar_top_height_m(config)
        linear_c_sq = gap / -float(np.min(linear.surface_per_circulation_sq))
        continued = solve_first_contact(
            solver,
            static_gap_m=gap,
            circulation_coupling_m2=config.forward.circulation_coupling_m2,
            linear_critical_circulation_sq_m4_s2=linear_c_sq,
        )

        # Independent state representation: u=x*q rather than a=atanh(q),
        # with W and pressure solved simultaneously at fixed y(0)=-S/c.
        coordinate = np.linspace(0.0, solver.radius_ratio, 400)
        elevation = np.interp(
            coordinate,
            solver.output_x,
            continued.surface.elevation_m / solver.core_radius_m,
        )
        q = np.interp(
            coordinate,
            solver.output_x,
            continued.surface.normalised_slope,
        )
        radial_q = coordinate * q
        integrand = coordinate * elevation
        volume = np.zeros_like(coordinate)
        volume[1:] = np.cumsum(
            0.5 * (integrand[:-1] + integrand[1:]) * np.diff(coordinate)
        )
        guess = np.vstack((elevation, radial_q, volume))
        target_axis = -gap / solver.core_radius_m

        def equations(x: np.ndarray, y: np.ndarray, p: np.ndarray) -> np.ndarray:
            ratio = np.divide(y[1], x, out=np.zeros_like(x), where=x > 0.0)
            ratio = np.clip(ratio, -0.999999999, 0.999999999)
            return np.vstack(
                (
                    ratio / np.sqrt(1.0 - np.square(ratio)),
                    x
                    * (
                        solver.bond_number * y[0]
                        - p[1] * burgers_dimensionless_potential(x)
                        - p[0]
                    ),
                    x * y[0],
                )
            )

        def boundary(
            left: np.ndarray, right: np.ndarray, p: np.ndarray
        ) -> np.ndarray:
            del p
            return np.array(
                [left[1], right[1], left[2], right[2], left[0] - target_axis]
            )

        direct = solve_bvp(
            equations,
            boundary,
            coordinate,
            guess,
            p=np.array(
                [
                    continued.surface.dimensionless_pressure,
                    continued.dimensionless_forcing,
                ]
            ),
            tol=1.0e-8,
            bc_tol=1.0e-9,
            max_nodes=20_000,
        )
        self.assertTrue(direct.success, direct.message)
        self.assertAlmostEqual(
            float(direct.p[1]), continued.dimensionless_forcing, delta=5.0e-4
        )
        self.assertLess(float(np.max(direct.rms_residuals)), 1.1e-8)

    def test_collocation_failure_is_raised_without_linear_fabrication(self) -> None:
        _, _, solver = configured_solver(points=128)
        failed = SimpleNamespace(
            success=False,
            status=1,
            message="synthetic maximum-node failure",
        )
        with patch(
            "airvortex.backends.nonlinear_surface.solve_bvp",
            return_value=failed,
        ):
            with self.assertRaisesRegex(
                NonlinearSurfaceError, "failed closed.*synthetic"
            ):
                solver.solve_forcing(2.0)

    def test_warm_started_surface_solve_meets_interactive_performance_gate(self) -> None:
        _, _, solver = configured_solver(
            points=128,
            relative_tolerance=1.0e-6,
            absolute_tolerance=1.0e-9,
        )
        seed = solver.solve_forcing(96.0)
        started = time.perf_counter()
        solution = solver.solve_forcing(100.0, initial=seed)
        elapsed = time.perf_counter() - started
        self.assertLess(elapsed, 2.0)
        self.assertLessEqual(
            solution.maximum_collocation_rms_residual,
            1.25 * solver.collocation_tolerance,
        )


if __name__ == "__main__":
    unittest.main()
