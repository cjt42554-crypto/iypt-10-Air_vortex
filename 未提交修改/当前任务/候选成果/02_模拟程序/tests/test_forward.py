from __future__ import annotations

import ast
from dataclasses import replace
import inspect
import math
from pathlib import Path
import sys
import unittest

import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from airvortex import (  # noqa: E402
    Backend,
    BackendMode,
    ContainerConfig,
    DriveConfig,
    FluidConfig,
    ForwardModelConfig,
    RunStatus,
    RunConfig,
    SimulationConfig,
)
from airvortex.backends import (  # noqa: E402
    ForwardBackend,
    NonlinearSurfaceSolver,
    UnderresolvedSurfaceError,
    critical_actual_rpm,
    linear_critical_actual_rpm,
    solve_unit_surface_response,
    stir_bar_top_height_m,
    surface_depth_functional_m,
    surface_profile_range_m,
)
import airvortex.backends.forward as forward_module  # noqa: E402


def make_config(
    rpm: float,
    *,
    initial_rpm: float | None = None,
    direction: int = 1,
    sigma: float = 0.072,
    duration_s: float = 0.2,
    output_interval_s: float = 0.05,
    spin_up_s: float = 0.4,
    surface_response_s: float = 0.0,
    radial_points: int = 128,
) -> SimulationConfig:
    if initial_rpm is None:
        initial_rpm = rpm
    fluid = replace(FluidConfig(), surface_tension_n_m=sigma)
    return SimulationConfig(
        backend=BackendMode.FORWARD,
        fluid=fluid,
        drive=DriveConfig(
            initial_rpm=initial_rpm,
            target_rpm=rpm,
            ramp_duration_s=0.0,
            rotation_direction=direction,
        ),
        run=RunConfig(
            duration_s=duration_s,
            output_interval_s=output_interval_s,
        ),
        forward=ForwardModelConfig(
            circulation_coupling_m2=9.0e-5,
            effective_core_radius_m=0.010,
            azimuthal_decay_power=1.0,
            spin_up_time_s=spin_up_s,
            surface_response_time_s=surface_response_s,
            air_core_depth_threshold_m=0.001,
            surface_radial_points=radial_points,
            integration_time_step_s=min(0.005, output_interval_s),
            relative_tolerance=1.0e-8,
            absolute_tolerance=1.0e-10,
        ),
    )


class ForwardPhysicsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.backend = ForwardBackend()

    def test_backend_contract_and_zero_speed_flat_state(self) -> None:
        self.assertIsInstance(self.backend, Backend)
        result = self.backend.run(make_config(0.0))

        self.assertEqual(result.backend, BackendMode.FORWARD)
        profile = result.observables["surface_profile_m"]
        np.testing.assert_allclose(profile, 0.060, rtol=0.0, atol=2.0e-14)
        np.testing.assert_allclose(result.observables["circulation_m2_s"], 0.0)
        np.testing.assert_allclose(result.observables["funnel_depth_m"], 0.0)
        expected_gap = 0.060 - stir_bar_top_height_m(make_config(0.0))
        np.testing.assert_allclose(result.observables["minimum_gap_m"], expected_gap)
        self.assertEqual(result.metadata["drive_interpretation"],
                         "prescribed_actual_stir_bar_speed")

    def test_augmented_surface_solve_and_run_conserve_volume(self) -> None:
        config = make_config(850.0)
        unit = solve_unit_surface_response(config)
        self.assertAlmostEqual(
            float(np.dot(unit.volume_weights, unit.surface_per_circulation_sq)),
            0.0,
            places=12,
        )

        result = self.backend.run(config)
        profiles = result.observables["surface_profile_m"]
        discrete_means = profiles @ result.fields["radial_volume_weights"]
        np.testing.assert_allclose(
            discrete_means,
            config.container.liquid_depth_m,
            rtol=0.0,
            atol=2.0e-14,
        )
        self.assertLess(
            float(np.max(np.abs(result.observables["liquid_volume_error_m3"]))),
            1.0e-16,
        )

    def test_linear_baseline_scales_as_omega_squared_but_nonlinear_main_does_not(self) -> None:
        low = self.backend.run(make_config(350.0))
        high = self.backend.run(make_config(700.0))
        low_depth = float(low.observables["funnel_depth_m"][-1])
        high_depth = float(high.observables["funnel_depth_m"][-1])
        self.assertGreater(low_depth, 0.0)
        self.assertGreater(high_depth / low_depth, 4.0)
        linear_low = float(low.observables["linear_funnel_depth_m"][-1])
        linear_high = float(high.observables["linear_funnel_depth_m"][-1])
        self.assertAlmostEqual(linear_high / linear_low, 4.0, places=11)

    def test_rotation_reversal_changes_circulation_sign_not_surface(self) -> None:
        positive = self.backend.run(make_config(720.0, direction=1))
        negative = self.backend.run(make_config(720.0, direction=-1))
        np.testing.assert_allclose(
            positive.observables["surface_profile_m"],
            negative.observables["surface_profile_m"],
            rtol=2.0e-13,
            atol=2.0e-14,
        )
        np.testing.assert_allclose(
            positive.observables["circulation_m2_s"],
            -negative.observables["circulation_m2_s"],
            rtol=2.0e-13,
            atol=1.0e-15,
        )
        np.testing.assert_allclose(
            positive.observables["funnel_depth_m"],
            negative.observables["funnel_depth_m"],
        )

    def test_surface_tension_weakens_the_central_depression(self) -> None:
        weak_capillarity = self.backend.run(make_config(600.0, sigma=0.020))
        strong_capillarity = self.backend.run(make_config(600.0, sigma=0.150))
        weak_depth = float(weak_capillarity.observables["funnel_depth_m"][-1])
        strong_depth = float(strong_capillarity.observables["funnel_depth_m"][-1])
        self.assertLess(strong_depth, weak_depth)
        self.assertGreater(
            float(strong_capillarity.observables["critical_actual_rpm"][-1]),
            float(weak_capillarity.observables["critical_actual_rpm"][-1]),
        )

    def test_linear_sigma_limit_and_finite_sigma_underresolution_are_explicit(self) -> None:
        from scipy.special import expi

        core_radius = 0.005
        vessel_radius = 0.100
        base = make_config(100.0, radial_points=256)
        config = replace(
            base,
            container=ContainerConfig(radius_m=vessel_radius, liquid_depth_m=0.060),
            fluid=replace(base.fluid, surface_tension_n_m=1.0e-10),
            forward=replace(
                base.forward,
                effective_core_radius_m=core_radius,
                surface_radial_points=256,
            ),
        )
        unit = solve_unit_surface_response(config)
        x = (vessel_radius / core_radius) ** 2
        finite_radius_potential = (
            -(-math.expm1(-x)) ** 2 / x
            + 2.0 * (expi(-x) - expi(-2.0 * x) + math.log(2.0))
        ) / (2.0 * core_radius**2)
        self.assertAlmostEqual(
            float(unit.potential_per_circulation_sq[-1]),
            float(finite_radius_potential),
            delta=2.0e-9 * finite_radius_potential,
        )
        numerical_unit_depth = float(np.ptp(unit.surface_per_circulation_sq))
        finite_radius_unit_depth = finite_radius_potential / config.fluid.gravity_m_s2
        self.assertAlmostEqual(
            numerical_unit_depth,
            finite_radius_unit_depth,
            delta=3.0e-8 * finite_radius_unit_depth,
        )
        large_radius_unit_depth = (
            math.log(2.0) / (config.fluid.gravity_m_s2 * core_radius**2)
        )
        self.assertAlmostEqual(
            numerical_unit_depth,
            large_radius_unit_depth,
            delta=0.003 * large_radius_unit_depth,
        )

        solver = NonlinearSurfaceSolver(
            config,
            linear_radius_m=unit.radius_m,
            linear_surface_per_circulation_sq=unit.surface_per_circulation_sq,
        )
        self.assertFalse(solver.passes_capillary_resolution_floor)
        with self.assertRaises(UnderresolvedSurfaceError):
            solver.solve_forcing(1.0)
        # The sigma->0 result remains available only as an explicit analytic
        # asymptotic reference, never as the finite-sigma Forward main path.
        singular = solver.solve_capillary_singular_limit(1.0)
        self.assertEqual(
            singular.solution_regime, "CAPILLARY_SINGULAR_LIMIT_OUTER"
        )
        with self.assertRaises(UnderresolvedSurfaceError):
            self.backend.run(config)

        singular_surface = (
            config.container.liquid_depth_m + singular.elevation_m
        )
        self.assertAlmostEqual(
            forward_module._half_width(unit.radius_m, singular_surface)
            / core_radius,
            1.06060,
            delta=0.003,
        )

    def test_circulation_step_response_obeys_closed_first_order_equation(self) -> None:
        tau = 0.4
        config = make_config(
            600.0,
            initial_rpm=0.0,
            duration_s=0.8,
            output_interval_s=0.1,
            spin_up_s=tau,
        )
        result = self.backend.run(config)
        target_omega = 600.0 * 2.0 * math.pi / 60.0
        target_circulation = (
            config.forward.circulation_coupling_m2 * target_omega
        )
        expected = target_circulation * (1.0 - np.exp(-result.time_s / tau))
        np.testing.assert_allclose(
            result.observables["circulation_m2_s"],
            expected,
            rtol=3.0e-13,
            atol=2.0e-15,
        )
        np.testing.assert_allclose(
            result.observables["surface_state_m4_s2"],
            np.square(expected),
            rtol=3.0e-13,
            atol=2.0e-18,
        )

    def test_optional_surface_response_delays_but_does_not_change_symmetry(self) -> None:
        instantaneous = self.backend.run(
            make_config(700.0, initial_rpm=0.0, surface_response_s=0.0)
        )
        delayed = self.backend.run(
            make_config(700.0, initial_rpm=0.0, surface_response_s=0.5)
        )
        self.assertLess(
            float(delayed.observables["funnel_depth_m"][-1]),
            float(instantaneous.observables["funnel_depth_m"][-1]),
        )
        reverse_delayed = self.backend.run(
            make_config(
                700.0,
                initial_rpm=0.0,
                direction=-1,
                surface_response_s=0.5,
            )
        )
        np.testing.assert_allclose(
            delayed.observables["surface_profile_m"],
            reverse_delayed.observables["surface_profile_m"],
            rtol=3.0e-13,
            atol=2.0e-14,
        )

    def test_nonlinear_critical_rpm_matches_bar_top_contact(self) -> None:
        template = make_config(1.0)
        threshold_rpm = critical_actual_rpm(template)
        below = self.backend.run(make_config(0.995 * threshold_rpm))
        at_threshold = self.backend.run(make_config(threshold_rpm))
        above = self.backend.run(make_config(1.005 * threshold_rpm))

        self.assertGreater(float(below.observables["minimum_gap_m"][-1]), 0.0)
        self.assertAlmostEqual(
            float(at_threshold.observables["surface_min_z_m"][-1]),
            stir_bar_top_height_m(template),
            delta=1.1
            * max(
                template.forward.absolute_tolerance,
                template.forward.relative_tolerance
                * (
                    template.container.liquid_depth_m
                    - stir_bar_top_height_m(template)
                ),
            ),
        )
        self.assertLess(float(above.observables["minimum_gap_m"][-1]), 0.0)
        for result in (below, at_threshold, above):
            self.assertAlmostEqual(
                float(result.observables["critical_actual_rpm"][-1]),
                threshold_rpm,
                delta=1.0e-8 * threshold_rpm,
            )
        self.assertLess(threshold_rpm, linear_critical_actual_rpm(template))
        self.assertEqual(
            at_threshold.metadata["critical_status"],
            "AVAILABLE_EXACT_CURVATURE",
        )

    def test_post_contact_geometry_is_finite_and_honestly_labelled(self) -> None:
        template = make_config(1.0)
        threshold_rpm = critical_actual_rpm(template)
        result = self.backend.run(make_config(1.15 * threshold_rpm))

        self.assertEqual(result.metadata["domain_status"], "OUT_OF_MODEL_DOMAIN")
        self.assertEqual(result.metadata["connection_status"], "CONNECTED_PROXY")
        self.assertEqual(
            result.metadata["small_slope_validity"],
            "LINEAR_BASELINE_OUTSIDE_ENGINEERING_WARNING_THRESHOLD",
        )
        self.assertGreater(
            float(result.observables["max_abs_surface_slope"][-1]), 0.3
        )
        self.assertEqual(float(result.observables["small_slope_validity"][-1]), 0.0)
        self.assertFalse(result.metadata["bubble_entrainment_simulated"])
        self.assertIn("small_slope_warning", result.metadata)
        empirical = result.metadata["diagnostics"]["empirical_reference"]
        self.assertTrue(empirical["is_empirical_reference"])
        self.assertFalse(empirical["universal_constant"])
        self.assertGreater(
            float(result.observables["air_core_bottom_radius_m"][-1]), 0.0
        )
        channel_z = result.fields["connected_proxy_channel_z_m"]
        channel_radius = result.fields["connected_proxy_channel_radius_m"]
        self.assertGreater(channel_z.size, 1)
        self.assertEqual(channel_z.shape, channel_radius.shape)
        self.assertTrue(np.all(np.diff(channel_z) >= 0.0))
        self.assertTrue(np.all(np.diff(channel_radius) >= -1.0e-14))
        self.assertTrue(any(event.kind == "CONNECTED_PROXY" for event in result.events))

        for array in tuple(result.observables.values()) + tuple(result.fields.values()):
            self.assertTrue(np.isfinite(array).all())

    def test_shared_funnel_depth_operator_is_not_profile_range(self) -> None:
        radius = np.linspace(0.0, 1.0, 11)
        surface = np.array(
            [0.0, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.3, 1.6]
        )
        depth = surface_depth_functional_m(radius, surface, 0.25)
        expected = float(np.mean(surface[radius >= 0.8]) - np.min(surface[radius <= 0.25]))
        self.assertAlmostEqual(depth, expected, places=15)
        self.assertAlmostEqual(surface_profile_range_m(surface), 1.6, places=15)
        self.assertNotAlmostEqual(depth, surface_profile_range_m(surface), places=8)

    def test_forward_functional_matches_h1_on_identical_sampled_profile(self) -> None:
        from airvortex.backends.high_fidelity import HighFidelityBackend
        from airvortex.numerics import (
            initialise_rotating_bucket_phase,
            surface_profile,
        )

        config = replace(make_config(0.0), backend=BackendMode.HIGH_FIDELITY)
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
        observed = backend._surface_observables(
            state, config, grid, grid.integrate(state.phase)
        )
        profile = surface_profile(state.phase, grid)
        forward_operator = surface_depth_functional_m(
            grid.r_centres, profile, config.stir_bar.half_length_m
        )
        self.assertAlmostEqual(
            forward_operator, float(observed["funnel_depth_m"]), places=15
        )
        self.assertAlmostEqual(
            surface_profile_range_m(profile),
            float(observed["surface_profile_range_m"]),
            places=15,
        )

    def test_deep_vessel_keeps_low_load_run_when_global_contact_is_unavailable(self) -> None:
        base = make_config(
            50.0,
            duration_s=0.02,
            output_interval_s=0.02,
            radial_points=128,
        )
        config = replace(
            base,
            container=replace(base.container, liquid_depth_m=0.60),
            forward=replace(
                base.forward,
                relative_tolerance=1.0e-5,
                absolute_tolerance=1.0e-8,
            ),
        )
        result = self.backend.run(config)
        self.assertEqual(result.status, RunStatus.COMPLETED)
        self.assertEqual(
            result.metadata["critical_status"], "UNAVAILABLE_GRAPH_BRANCH"
        )
        self.assertIn("single-valued-graph", result.metadata["critical_domain_reason"])
        self.assertNotIn("critical_actual_rpm", result.observables)
        self.assertGreater(float(result.observables["minimum_gap_m"][-1]), 0.0)
        self.assertEqual(result.metadata["domain_status"], "IN_MODEL_DOMAIN")

    def test_soft_stop_keeps_last_completed_partial_integration_state(self) -> None:
        class StopAfterSevenCheckpoints:
            stop_reason = "deterministic unit-test stop"

            def __init__(self) -> None:
                self.calls = 0

            def checkpoint(self) -> bool:
                self.calls += 1
                return self.calls <= 7

        control = StopAfterSevenCheckpoints()
        config = make_config(
            700.0,
            initial_rpm=0.0,
            duration_s=1.0,
            output_interval_s=0.2,
        )
        result = self.backend.run(config, control=control)  # type: ignore[arg-type]
        self.assertEqual(result.status, RunStatus.STOPPED)
        self.assertGreater(float(result.time_s[-1]), 0.0)
        self.assertLess(float(result.time_s[-1]), config.run.output_interval_s)
        self.assertAlmostEqual(
            float(result.time_s[-1]),
            7.0 * config.forward.integration_time_step_s,
            places=14,
        )
        self.assertEqual(result.events[-1].kind, "stopped")

    def test_forward_module_has_no_high_fidelity_solver_dependency(self) -> None:
        source = inspect.getsource(forward_module)
        tree = ast.parse(source)
        imported_modules: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.append(node.module)
        self.assertFalse(
            any("high_fidelity" in module for module in imported_modules),
            imported_modules,
        )
        self.assertFalse(
            any(result_name.startswith("hf_") for result_name in forward_module.__all__)
        )


if __name__ == "__main__":
    unittest.main()
