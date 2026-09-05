from __future__ import annotations

from dataclasses import replace
import json
import math
from pathlib import Path
import unittest

import numpy as np

from airvortex.numerics.gas_pressure import (
    AreaRateFrame,
    GasPathRegime,
    GasPressureStatus,
    OpenReservoirInput,
    PressureScaleInput,
    RestrictedChannelInput,
    UnavailableGasPathInput,
    convert_area_rate_to_fixed_space,
    diagnose_gas_pressure,
    estimate_area_rate_at_fixed_space,
)


REFERENCE_PATH = (
    Path(__file__).resolve().parent
    / "reference_data"
    / "gas_pressure_benchmarks.json"
)
with REFERENCE_PATH.open(encoding="utf-8") as stream:
    REFERENCE = json.load(stream)


def restricted_input(
    z_m: np.ndarray,
    area_m2: np.ndarray,
    area_rate_fixed_m2_s: np.ndarray | None,
    *,
    viscosity_pa_s: float = 1.8e-5,
    lower_speed_m_s: float | None = 0.0,
    lower_relative_flux_m3_s: float | None = 0.0,
    **changes: object,
) -> RestrictedChannelInput:
    values: dict[str, object] = {
        "z_m": z_m,
        "area_m2": area_m2,
        "area_rate_fixed_m2_s": area_rate_fixed_m2_s,
        "gas_viscosity_pa_s": viscosity_pa_s,
        "lower_boundary_speed_m_s": lower_speed_m_s,
        "lower_relative_flux_m3_s": lower_relative_flux_m3_s,
        "area_rate_frame": AreaRateFrame.FIXED_SPACE,
        "area_rate_source": "accepted-step manufactured profile",
        "lower_boundary_source": "manufactured lower-boundary kinematics",
        "upper_pressure_source": "prescribed atmospheric reservoir",
        "geometry_evidence_source": "manufactured single circular slender tube",
        "approximation_evidence_source": "F87 benchmark assumptions",
        "target_error_budget_source": "synthetic pressure benchmark budget",
        "single_channel": True,
        "near_circular": True,
        "slender": True,
        "upper_connected_to_atmosphere": True,
        "quasi_steady_viscous": True,
        "gas_inertia_negligible": True,
        "compressibility_negligible": True,
        "wall_resistance_applicable": True,
    }
    values.update(changes)
    return RestrictedChannelInput(**values)  # type: ignore[arg-type]


def observed_orders(errors: list[float]) -> np.ndarray:
    return np.log2(np.asarray(errors[:-1]) / np.asarray(errors[1:]))


class F86F87AnalyticBenchmarks(unittest.TestCase):
    def test_b1_zero_net_volume_change_has_internal_flow_and_second_order_pressure(
        self,
    ) -> None:
        case = REFERENCE["B1"]
        length = case["length_m"]
        radius = case["radius_m"]
        area_value = math.pi * radius**2
        amplitude = case["area_rate_amplitude_m2_s"]
        expected_pressure = case["max_abs_pressure_pa"]
        expected_midpoint_flow = case["midpoint_flow_m3_s"]
        pressure_errors: list[float] = []
        flow_errors: list[float] = []

        for segments in REFERENCE["quadrature_segments"]:
            z = np.linspace(0.0, length, segments + 1)
            area = np.full_like(z, area_value)
            area_rate = amplitude * np.sin(2.0 * math.pi * z / length)
            result = diagnose_gas_pressure(
                restricted_input(
                    z,
                    area,
                    area_rate,
                    viscosity_pa_s=case["gas_viscosity_pa_s"],
                )
            )
            self.assertEqual(result.status, GasPressureStatus.EVALUATED)
            self.assertEqual(result.regime, GasPathRegime.RESTRICTED_CHANNEL)
            self.assertAlmostEqual(result.upper_flow_m3_s, 0.0, delta=2.0e-20)
            pressure_errors.append(
                abs(result.max_abs_pressure_pa - expected_pressure)
                / expected_pressure
            )
            midpoint = segments // 2
            flow_errors.append(
                abs(result.flow_m3_s[midpoint] - expected_midpoint_flow)
                / abs(expected_midpoint_flow)
            )
            self.assertGreater(
                result.pressure_upper_bound_pa, result.max_abs_pressure_pa
            )
            self.assertEqual(result.pressure_relative_pa[-1], 0.0)
            self.assertFalse(result.flow_m3_s.flags.writeable)
            self.assertEqual(
                result.area_rate_source, "accepted-step manufactured profile"
            )
            self.assertIsNone(result.pressure_budget_satisfied)

        self.assertLessEqual(pressure_errors[-1], 1.0e-3)
        self.assertLessEqual(flow_errors[-1], 1.0e-3)
        self.assertTrue(np.all(observed_orders(pressure_errors) > 1.95))
        self.assertTrue(np.all(observed_orders(flow_errors) > 1.95))

    def test_b2_equal_endpoint_pressures_do_not_hide_internal_pressure(self) -> None:
        case = REFERENCE["B2"]
        length = case["length_m"]
        radius = case["radius_m"]
        flow_amplitude = case["flow_amplitude_m3_s"]
        area_value = math.pi * radius**2
        expected_pressure = case["max_abs_pressure_pa"]
        pressure_errors: list[float] = []

        for segments in REFERENCE["quadrature_segments"]:
            z = np.linspace(0.0, length, segments + 1)
            area = np.full_like(z, area_value)
            area_rate = -(
                2.0 * math.pi * flow_amplitude / length
            ) * np.cos(2.0 * math.pi * z / length)
            result = diagnose_gas_pressure(
                restricted_input(
                    z,
                    area,
                    area_rate,
                    viscosity_pa_s=case["gas_viscosity_pa_s"],
                    prescribed_upper_flow_m3_s=0.0,
                    upper_flow_tolerance_m3_s=1.0e-15,
                )
            )
            self.assertEqual(result.status, GasPressureStatus.EVALUATED)
            self.assertAlmostEqual(
                result.lower_minus_upper_pressure_pa,
                case["endpoint_pressure_difference_pa"],
                delta=3.0e-12,
            )
            self.assertGreater(result.max_abs_pressure_pa, 1.0)
            pressure_errors.append(
                abs(result.max_abs_pressure_pa - expected_pressure)
                / expected_pressure
            )

        self.assertLessEqual(pressure_errors[-1], 1.0e-3)
        self.assertTrue(np.all(observed_orders(pressure_errors) > 1.95))


class FixedSpaceAndMovingBoundaryTests(unittest.TestCase):
    def test_grid_coordinate_rate_is_converted_to_fixed_space(self) -> None:
        z = np.linspace(0.0, 0.1, 33)
        axial_gradient = 2.0e-5
        fixed_rate = -7.0e-6
        grid_velocity = np.full_like(z, 0.012)
        area = 4.0e-6 + axial_gradient * z
        grid_coordinate_rate = np.full_like(
            z, fixed_rate + grid_velocity[0] * axial_gradient
        )
        converted = convert_area_rate_to_fixed_space(
            z, area, grid_coordinate_rate, grid_velocity
        )
        np.testing.assert_allclose(
            converted, fixed_rate, atol=2.0e-19, rtol=0.0
        )
        self.assertFalse(converted.flags.writeable)

    def test_accepted_step_area_rate_has_second_order_time_convergence(
        self,
    ) -> None:
        case = REFERENCE["fixed_space_area_rate"]
        time = case["evaluation_time_s"]
        offset = case["area_offset_m2"]
        axial_gradient = case["area_axial_gradient_m"]
        amplitude = case["area_time_amplitude_m2"]
        rate = case["exponential_rate_s_inv"]
        grid_velocity = case["grid_velocity_m_s"]
        base_grid = np.linspace(0.0, 0.1, 257)
        evaluation_z = np.linspace(0.01, 0.09, 129)
        expected = rate * amplitude * math.exp(rate * time)
        errors: list[float] = []

        def area(z: np.ndarray, at_time: float) -> np.ndarray:
            return offset + axial_gradient * z + amplitude * np.exp(rate * at_time)

        for half_interval in case["half_intervals_s"]:
            previous_time = time - half_interval
            next_time = time + half_interval
            previous_z = base_grid + grid_velocity * previous_time
            next_z = base_grid + grid_velocity * next_time
            estimate = estimate_area_rate_at_fixed_space(
                previous_z,
                area(previous_z, previous_time),
                next_z,
                area(next_z, next_time),
                evaluation_z,
                2.0 * half_interval,
            )
            errors.append(float(np.max(np.abs(estimate - expected))) / expected)

        self.assertLess(errors[-1], 1.0e-4)
        self.assertTrue(np.all(observed_orders(errors) > 1.95))

    def test_moving_lower_section_and_relative_flux_are_both_in_f86(self) -> None:
        z = np.linspace(0.02, 0.12, 65)
        area = np.full_like(z, 2.5e-6)
        area_rate = np.zeros_like(z)
        lower_speed = 0.03
        relative_flux = -1.2e-8
        result = diagnose_gas_pressure(
            restricted_input(
                z,
                area,
                area_rate,
                lower_speed_m_s=lower_speed,
                lower_relative_flux_m3_s=relative_flux,
            )
        )
        expected_flow = area[0] * lower_speed + relative_flux
        np.testing.assert_allclose(result.flow_m3_s, expected_flow, atol=1.0e-22)
        self.assertAlmostEqual(result.lower_flow_m3_s, expected_flow)


class F87aF88ScreenTests(unittest.TestCase):
    def test_f88_uses_conditional_pressure_upper_bound_not_curve_maximum(self) -> None:
        z = np.linspace(0.0, 0.1, 257)
        radius = 1.0e-3
        area = np.full_like(z, math.pi * radius**2)
        area_rate = 1.0e-4 * np.sin(2.0 * math.pi * z / z[-1])
        scale = PressureScaleInput(
            surface_tension_n_m=0.072,
            density_difference_kg_m3=997.0,
            gravity_m_s2=9.80665,
            centrifugal_potential_range_m2_s2=0.01,
            liquid_density_kg_m3=998.2,
            epsilon_g_max=1.0,
        )
        passed = diagnose_gas_pressure(
            restricted_input(z, area, area_rate, pressure_scale=scale)
        )
        self.assertEqual(passed.status, GasPressureStatus.EVALUATED)
        self.assertTrue(passed.epsilon_limit_satisfied)
        self.assertGreater(passed.pressure_upper_bound_pa, passed.max_abs_pressure_pa)
        self.assertAlmostEqual(
            passed.epsilon_g,
            passed.pressure_upper_bound_pa / passed.pressure_reference_pa,
            places=15,
        )
        self.assertIn("f88_conditional_screen_passed", passed.reasons)
        self.assertEqual(
            passed.target_error_budget_source, "synthetic pressure benchmark budget"
        )

        failed = diagnose_gas_pressure(
            restricted_input(
                z,
                area,
                area_rate,
                pressure_scale=replace(scale, epsilon_g_max=passed.epsilon_g / 2.0),
            )
        )
        self.assertEqual(failed.status, GasPressureStatus.ASSUMPTION_FAILED)
        self.assertFalse(failed.epsilon_limit_satisfied)
        self.assertGreater(failed.max_abs_pressure_pa, 0.0)

    def test_derivative_error_and_pressure_budget_are_a_separate_screen(self) -> None:
        z = np.linspace(0.0, 0.1, 129)
        area = np.full_like(z, math.pi * 1.0e-3**2)
        area_rate = 1.0e-4 * np.sin(2.0 * math.pi * z / z[-1])
        baseline = diagnose_gas_pressure(restricted_input(z, area, area_rate))
        derivative_error = 0.25
        budget = baseline.pressure_upper_bound_pa + derivative_error + 0.01
        passed = diagnose_gas_pressure(
            restricted_input(
                z,
                area,
                area_rate,
                derivative_pressure_error_bound_pa=derivative_error,
                pressure_error_budget_pa=budget,
            )
        )
        self.assertEqual(passed.status, GasPressureStatus.EVALUATED)
        self.assertTrue(passed.pressure_budget_satisfied)
        self.assertAlmostEqual(
            passed.combined_pressure_bound_pa,
            passed.pressure_upper_bound_pa + derivative_error,
        )

        failed = diagnose_gas_pressure(
            restricted_input(
                z,
                area,
                area_rate,
                derivative_pressure_error_bound_pa=derivative_error,
                pressure_error_budget_pa=budget - 0.02,
            )
        )
        self.assertEqual(failed.status, GasPressureStatus.ASSUMPTION_FAILED)
        self.assertFalse(failed.pressure_budget_satisfied)
        self.assertGreater(failed.max_abs_pressure_pa, 0.0)


class FailClosedBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.z = np.linspace(0.0, 0.1, 17)
        self.area = np.full_like(self.z, math.pi * 1.0e-3**2)
        self.zero_rate = np.zeros_like(self.z)
        self.base = restricted_input(self.z, self.area, self.zero_rate)

    def assert_no_channel_output(self, result: object) -> None:
        self.assertEqual(result.z_m.size, 0)
        self.assertEqual(result.flow_m3_s.size, 0)
        self.assertEqual(result.pressure_relative_pa.size, 0)
        self.assertIsNone(result.max_abs_pressure_pa)

    def test_constant_stationary_channel_is_evaluated_as_zero(self) -> None:
        result = diagnose_gas_pressure(self.base)
        self.assertEqual(result.status, GasPressureStatus.EVALUATED)
        np.testing.assert_array_equal(result.flow_m3_s, 0.0)
        np.testing.assert_array_equal(result.pressure_relative_pa, 0.0)
        self.assertEqual(result.max_abs_pressure_pa, 0.0)

    def test_open_reservoir_needs_evidence_but_never_needs_radius(self) -> None:
        missing = diagnose_gas_pressure(OpenReservoirInput())
        self.assertEqual(missing.status, GasPressureStatus.UNVERIFIED)
        self.assert_no_channel_output(missing)

        passed = diagnose_gas_pressure(
            OpenReservoirInput(
                pressure_error_bound_pa=0.2,
                pressure_reference_pa=100.0,
                pressure_error_budget_pa=1.0,
                epsilon_g_max=0.01,
                evidence_source="independent open-headspace estimate",
            )
        )
        self.assertEqual(passed.status, GasPressureStatus.EVALUATED)
        self.assertTrue(passed.epsilon_limit_satisfied)
        self.assertEqual(passed.max_abs_pressure_pa, 0.2)
        self.assertTrue(passed.pressure_budget_satisfied)
        self.assertEqual(passed.z_m.size, 0)
        self.assertEqual(passed.flow_m3_s.size, 0)
        self.assertEqual(passed.pressure_relative_pa.size, 0)

    def test_unknown_flow_total_volume_and_first_sample_stay_unverified(self) -> None:
        unknown_flow = diagnose_gas_pressure(
            replace(self.base, lower_relative_flux_m3_s=None)
        )
        first_sample = diagnose_gas_pressure(
            replace(self.base, area_rate_fixed_m2_s=None)
        )
        total_volume_only = diagnose_gas_pressure(
            UnavailableGasPathInput(
                GasPathRegime.RESTRICTED_CHANNEL,
                "only_total_connected_gas_volume_available",
            )
        )
        for result in (unknown_flow, first_sample, total_volume_only):
            self.assertEqual(result.status, GasPressureStatus.UNVERIFIED)
            self.assert_no_channel_output(result)

    def test_incomplete_budget_keeps_computed_profiles_unverified(self) -> None:
        only_budget = diagnose_gas_pressure(
            replace(self.base, pressure_error_budget_pa=100.0)
        )
        only_derivative_error = diagnose_gas_pressure(
            replace(self.base, derivative_pressure_error_bound_pa=0.0)
        )
        for result in (only_budget, only_derivative_error):
            self.assertEqual(result.status, GasPressureStatus.UNVERIFIED)
            self.assertIn("pressure_budget_inputs_incomplete", result.reasons)
            self.assertEqual(result.z_m.size, self.z.size)
            np.testing.assert_array_equal(result.flow_m3_s, 0.0)
            np.testing.assert_array_equal(result.pressure_relative_pa, 0.0)
            self.assertIsNone(result.pressure_budget_satisfied)

    def test_unconverted_moving_grid_rate_stays_unverified(self) -> None:
        result = diagnose_gas_pressure(
            replace(self.base, area_rate_frame=AreaRateFrame.GRID_COORDINATE)
        )
        self.assertEqual(result.status, GasPressureStatus.UNVERIFIED)
        self.assertIn("not_converted", result.reasons[0])
        self.assert_no_channel_output(result)

    def test_thin_slit_branch_and_zero_radius_are_not_applicable(self) -> None:
        cases = (
            replace(self.base, near_circular=False),
            replace(self.base, single_channel=False),
            replace(self.base, area_m2=np.zeros_like(self.area)),
        )
        for case in cases:
            with self.subTest(reason=case):
                result = diagnose_gas_pressure(case)
                self.assertEqual(result.status, GasPressureStatus.NOT_APPLICABLE)
                self.assert_no_channel_output(result)

    def test_closed_gas_requires_another_pressure_mass_model(self) -> None:
        result = diagnose_gas_pressure(
            UnavailableGasPathInput(
                GasPathRegime.CLOSED_GAS, "initial_closed_gas_component"
            )
        )
        self.assertEqual(result.status, GasPressureStatus.ASSUMPTION_FAILED)
        self.assert_no_channel_output(result)

    def test_inconsistent_independent_upper_flow_fails_before_pressure(self) -> None:
        result = diagnose_gas_pressure(
            replace(
                self.base,
                prescribed_upper_flow_m3_s=1.0e-6,
                upper_flow_tolerance_m3_s=1.0e-12,
            )
        )
        self.assertEqual(result.status, GasPressureStatus.ASSUMPTION_FAILED)
        self.assertIn("inconsistent", result.reasons[0])
        self.assert_no_channel_output(result)

    def test_invalid_inputs_produce_no_physical_output(self) -> None:
        cases = (
            replace(self.base, gas_viscosity_pa_s=math.nan),
            replace(self.base, gas_viscosity_pa_s=-1.0),
            replace(self.base, z_m=self.z[::-1]),
            replace(self.base, area_m2=-self.area),
        )
        for case in cases:
            with self.subTest(reason=case):
                result = diagnose_gas_pressure(case)
                self.assertEqual(result.status, GasPressureStatus.INVALID_INPUT)
                self.assert_no_channel_output(result)

    def test_fixed_space_snapshot_estimator_rejects_extrapolation(self) -> None:
        with self.assertRaisesRegex(ValueError, "common physical domain"):
            estimate_area_rate_at_fixed_space(
                self.z,
                self.area,
                self.z + 0.01,
                self.area,
                self.z,
                0.01,
            )


if __name__ == "__main__":
    unittest.main()
