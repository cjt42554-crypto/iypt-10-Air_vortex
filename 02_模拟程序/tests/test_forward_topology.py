from __future__ import annotations

from dataclasses import replace
import math
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
from scipy.integrate import quad


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from airvortex.backends.forward_topology import (  # noqa: E402
    ForwardState,
    ForwardTopologyBackend,
    actual_bar_omega_rad_s,
    advance_circulation_exact,
    burgers_potential_shape,
    load_forward_state_npz,
    save_forward_state_npz,
)
from airvortex.core import (  # noqa: E402
    BackendMode,
    DriveConfig,
    RunConfig,
    SimulationConfig,
)
from airvortex.numerics.axisymmetric import AxisymmetricGrid  # noqa: E402
from airvortex.numerics.variational_interface import (  # noqa: E402
    FLOOR,
    TARGET,
    WALL,
    ContactLaw,
    build_variational_geometry,
    discrete_energy_and_gradient,
    initialise_flat_order_parameter,
    liquid_volume_m3,
    topology_diagnostics,
)
from airvortex.runtime import discover_backends  # noqa: E402


from validity_fixtures import ideal_test_reservoir


def compact_config(
    *,
    rpm: float = 0.0,
    direction: int = 1,
    duration_s: float = 0.02,
    output_s: float = 0.01,
    ramp_s: float = 0.0,
    initial_circulation: float = 0.0,
) -> SimulationConfig:
    base = SimulationConfig()
    return replace(
        base,
        backend=BackendMode.FORWARD,
        gas=ideal_test_reservoir(until_s=duration_s),
        drive=DriveConfig(
            initial_rpm=rpm if ramp_s == 0.0 else 0.0,
            target_rpm=rpm,
            ramp_duration_s=ramp_s,
            rotation_direction=direction,
        ),
        run=RunConfig(duration_s=duration_s, output_interval_s=output_s),
        forward=replace(
            base.forward,
            initial_circulation_m2_s=initial_circulation,
            radial_cells=24,
            axial_cells=40,
            integration_time_step_s=0.002,
            interface_thickness_cells=1.25,
            minimum_resolved_throat_cells=3,
            stable_channel_radius_m=0.001,
            stable_channel_hold_time_s=0.2,
        ),
    )


def geometry_for(config: SimulationConfig):
    grid = AxisymmetricGrid(
        config.forward.radial_cells,
        config.forward.axial_cells,
        config.container.radius_m,
        config.container.liquid_depth_m + config.container.headspace_height_m,
    )
    geometry = build_variational_geometry(
        grid,
        liquid_height_m=config.container.liquid_depth_m,
        target_bottom_m=config.stir_bar.bottom_clearance_m,
        target_top_m=(
            config.stir_bar.bottom_clearance_m + 2.0 * config.stir_bar.radius_m
        ),
        target_radius_m=config.forward.effective_target_radius_m,
        top_seed_depth_m=config.forward.top_seed_depth_m,
    )
    epsilon = config.forward.interface_thickness_cells * max(grid.dr, grid.dz)
    return grid, geometry, epsilon


def laws(config: SimulationConfig):
    f = config.forward

    def one(prefix: str) -> ContactLaw:
        return ContactLaw(
            math.radians(getattr(f, f"{prefix}_receding_angle_deg")),
            math.radians(getattr(f, f"{prefix}_equilibrium_angle_deg")),
            math.radians(getattr(f, f"{prefix}_advancing_angle_deg")),
            getattr(f, f"{prefix}_contact_friction_pa_s"),
        )

    return {WALL: one("wall"), FLOOR: one("floor"), TARGET: one("target")}


class ForwardTopologyNumericsTests(unittest.TestCase):
    def test_default_registry_selects_normative_topology_backend(self) -> None:
        backend = discover_backends().create("forward")
        self.assertIsInstance(backend, ForwardTopologyBackend)
        self.assertIn("F1.0", backend.display_name)

    def test_flat_initialisation_matches_discrete_volume_contract(self) -> None:
        config = compact_config()
        _, geometry, epsilon = geometry_for(config)
        phase = initialise_flat_order_parameter(
            geometry,
            epsilon,
            volume_relative_tolerance=config.forward.volume_relative_tolerance,
        )
        volume = liquid_volume_m3(phase, geometry)
        self.assertLessEqual(
            abs(volume - geometry.target_liquid_volume_m3),
            config.forward.volume_relative_tolerance
            * geometry.target_liquid_volume_m3,
        )
        self.assertTrue(np.all(phase[geometry.top_reservoir_mask] == -1.0))
        self.assertTrue(np.all(phase[geometry.target_mask] == 1.0))

    def test_burgers_potential_shape_matches_independent_quadrature(self) -> None:
        radii = np.array([0.0, 1.0e-4, 0.02, 0.2, 1.0, 4.5, 20.0])
        actual = burgers_potential_shape(radii)
        expected = []
        for x in radii:
            if x == 0.0:
                expected.append(0.0)
            else:
                expected.append(
                    quad(
                        lambda s: (1.0 - math.exp(-s * s)) ** 2 / s**3,
                        0.0,
                        float(x),
                        epsabs=1.0e-13,
                        epsrel=1.0e-13,
                    )[0]
                )
        np.testing.assert_allclose(actual, expected, rtol=3.0e-11, atol=2.0e-14)
        self.assertAlmostEqual(float(burgers_potential_shape(np.array([1.0e4]))[0]), math.log(2.0), places=8)

    def test_circulation_update_is_exact_for_constant_and_linear_drive(self) -> None:
        base = compact_config(rpm=900.0)
        omega = actual_bar_omega_rad_s(base, 0.0)
        initial = 0.003
        dt = 0.37
        actual = advance_circulation_exact(initial, base, 0.0, dt)
        target = base.forward.circulation_coupling_m2 * omega
        expected = target + (initial - target) * math.exp(
            -dt / base.forward.spin_up_time_s
        )
        self.assertAlmostEqual(actual, expected, places=15)

        ramp = compact_config(rpm=900.0, ramp_s=1.0, duration_s=1.0)
        # Independent high-accuracy quadrature of F22 for a ramp segment.
        forcing = quad(
            lambda s: (
                math.exp(-(dt - s) / ramp.forward.spin_up_time_s)
                * ramp.forward.circulation_coupling_m2
                / ramp.forward.spin_up_time_s
                * actual_bar_omega_rad_s(ramp, s)
            ),
            0.0,
            dt,
            epsabs=1.0e-15,
            epsrel=1.0e-13,
        )[0]
        expected_ramp = initial * math.exp(-dt / ramp.forward.spin_up_time_s) + forcing
        self.assertAlmostEqual(
            advance_circulation_exact(initial, ramp, 0.0, dt),
            expected_ramp,
            places=14,
        )

    def test_signed_piecewise_drive_round_trips_and_crosses_reversal(self) -> None:
        base = SimulationConfig()
        schedule = DriveConfig(
            actual_speed_schedule_time_s=(0.0, 0.2, 0.5, 1.0),
            actual_speed_schedule_rpm=(0.0, 900.0, -900.0, 0.0),
        )
        config = replace(
            base,
            drive=schedule,
            run=RunConfig(duration_s=1.0, output_interval_s=0.1),
        )
        self.assertEqual(config, SimulationConfig.from_dict(config.to_dict()))
        self.assertAlmostEqual(
            actual_bar_omega_rad_s(config, 0.35), 0.0, delta=3.0e-14
        )
        dt = 0.8
        expected = quad(
            lambda s: (
                math.exp(-(dt - s) / config.forward.spin_up_time_s)
                * config.forward.circulation_coupling_m2
                / config.forward.spin_up_time_s
                * actual_bar_omega_rad_s(config, s)
            ),
            0.0,
            dt,
            points=(0.2, 0.5),
            epsabs=1.0e-14,
            epsrel=1.0e-12,
        )[0]
        self.assertAlmostEqual(
            advance_circulation_exact(0.0, config, 0.0, dt),
            expected,
            places=13,
        )

    def test_fixed_drive_energy_and_volume_gates_pass(self) -> None:
        base = compact_config(rpm=800.0, duration_s=0.04, output_s=0.01)
        equilibrium_c = (
            base.forward.circulation_coupling_m2
            * actual_bar_omega_rad_s(base, 0.0)
        )
        config = replace(
            base,
            forward=replace(
                base.forward, initial_circulation_m2_s=equilibrium_c
            ),
        )
        result = ForwardTopologyBackend().run(config)
        energy = result.observables["forward_effective_energy_j"]
        self.assertTrue(np.all(np.diff(energy) <= 1.0e-12))
        self.assertTrue(result.metadata["discretisation"]["volume_gate_passed"])
        self.assertTrue(result.metadata["discretisation"]["energy_gate_passed"])

    def test_rotation_reversal_changes_c_sign_but_not_interface(self) -> None:
        positive = compact_config(rpm=800.0, duration_s=0.02, output_s=0.01)
        c0 = positive.forward.circulation_coupling_m2 * actual_bar_omega_rad_s(
            positive, 0.0
        )
        positive = replace(
            positive,
            forward=replace(positive.forward, initial_circulation_m2_s=c0),
        )
        negative = compact_config(
            rpm=800.0,
            direction=-1,
            duration_s=0.02,
            output_s=0.01,
            initial_circulation=-c0,
        )
        result_positive = ForwardTopologyBackend().run(positive)
        result_negative = ForwardTopologyBackend().run(negative)
        np.testing.assert_allclose(
            result_positive.observables["burgers_C_m2_s"],
            -result_negative.observables["burgers_C_m2_s"],
            rtol=0.0,
            atol=2.0e-15,
        )
        np.testing.assert_allclose(
            result_positive.fields["final_phase_order_parameter"],
            result_negative.fields["final_phase_order_parameter"],
            rtol=0.0,
            atol=2.0e-14,
        )

    def test_restart_npz_is_hash_and_geometry_bound(self) -> None:
        config = compact_config(rpm=600.0, duration_s=0.01, output_s=0.01)
        result = ForwardTopologyBackend().run(config)
        state = ForwardState.from_result(result)
        with tempfile.TemporaryDirectory() as temporary:
            path, digest = save_forward_state_npz(
                Path(temporary) / "state.npz", state, config
            )
            loaded = load_forward_state_npz(path, digest, config)
            np.testing.assert_array_equal(
                loaded.phase_order_parameter, state.phase_order_parameter
            )
            self.assertEqual(loaded.circulation_m2_s, state.circulation_m2_s)
            configured = replace(
                config,
                forward=replace(
                    config.forward,
                    initial_state_npz_path=str(path),
                    initial_state_sha256=digest,
                ),
            )
            restarted = ForwardTopologyBackend().run(configured)
            self.assertEqual(
                restarted.metadata["initial_state_kind"],
                "verified_npz_forward_state",
            )
            np.testing.assert_array_equal(
                restarted.fields["initial_phase_order_parameter"],
                state.phase_order_parameter,
            )
            incompatible = replace(
                config,
                forward=replace(config.forward, radial_cells=32),
            )
            with self.assertRaisesRegex(ValueError, "geometry mismatch"):
                load_forward_state_npz(path, digest, incompatible)
            with path.open("ab") as stream:
                stream.write(b"tamper")
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                load_forward_state_npz(path, digest, config)

    def test_restart_save_rejects_invalid_volume_and_fixed_regions(self) -> None:
        config = compact_config()
        grid, geometry, epsilon = geometry_for(config)
        phase = initialise_flat_order_parameter(geometry, epsilon)
        with tempfile.TemporaryDirectory() as temporary:
            invalid_top = phase.copy()
            top_i, top_j = np.argwhere(geometry.top_reservoir_mask)[0]
            invalid_top[top_i, top_j] = 1.0
            with self.assertRaisesRegex(ValueError, "top gas reservoir"):
                save_forward_state_npz(
                    Path(temporary) / "invalid-top.npz",
                    ForwardState(0.0, invalid_top),
                    config,
                )

            invalid_volume = phase.copy()
            fluid_cells = np.argwhere(
                geometry.fluid_mask & ~geometry.top_reservoir_mask
            )
            liquid_i, liquid_j = next(
                (int(i), int(j))
                for i, j in fluid_cells
                if invalid_volume[int(i), int(j)] > 0.99
            )
            invalid_volume[liquid_i, liquid_j] = -1.0
            with self.assertRaisesRegex(ValueError, "liquid volume"):
                save_forward_state_npz(
                    Path(temporary) / "invalid-volume.npz",
                    ForwardState(0.0, invalid_volume),
                    config,
                )

    def test_one_cell_path_is_not_a_resolved_channel(self) -> None:
        config = compact_config()
        grid, geometry, epsilon = geometry_for(config)
        phase = np.ones(grid.shape, dtype=float)
        phase[geometry.target_mask] = 1.0
        phase[geometry.top_reservoir_mask] = -1.0
        target_rows = np.argwhere(geometry.target_mask)[:, 1]
        start = int(np.max(target_rows)) + 1
        phase[0, start:] = -1.0
        diagnostic = topology_diagnostics(
            phase,
            geometry,
            interface_thickness_m=epsilon,
            minimum_resolved_throat_cells=3,
        )
        self.assertTrue(diagnostic.touches_target)
        self.assertTrue(diagnostic.finite_channel_candidate)
        self.assertFalse(diagnostic.resolved_channel)
        self.assertAlmostEqual(diagnostic.minimum_throat_radius_m, grid.dr)

        phase[:8, start:] = -1.0
        resolved = topology_diagnostics(
            phase,
            geometry,
            interface_thickness_m=epsilon,
            minimum_resolved_throat_cells=3,
        )
        self.assertTrue(resolved.resolved_channel)
        self.assertGreaterEqual(
            resolved.minimum_throat_radius_m,
            resolved.required_resolved_radius_m,
        )

    def test_flat_side_contact_is_pinned_inside_hysteresis_window(self) -> None:
        config = compact_config()
        grid, geometry, epsilon = geometry_for(config)
        phase = initialise_flat_order_parameter(geometry, epsilon)
        z = grid.z_centres[None, :]
        delta_density = (
            config.fluid.liquid_density_kg_m3
            - config.fluid.gas_density_kg_m3
        )
        potential = np.broadcast_to(
            delta_density
            * config.fluid.gravity_m_s2
            * (z - config.container.liquid_depth_m),
            grid.shape,
        )
        _, _, _, pinned_cells, diagnostic = discrete_energy_and_gradient(
            phase,
            geometry,
            potential,
            surface_tension_n_m=config.fluid.surface_tension_n_m,
            interface_thickness_m=epsilon,
            contact_laws=laws(config),
        )
        self.assertGreater(diagnostic.pinned_faces, 0)
        self.assertEqual(diagnostic.advancing_faces, 0)
        self.assertEqual(diagnostic.receding_faces, 0)
        self.assertTrue(np.any(pinned_cells[-1, :]))


class ForwardTopologyTransitionTests(unittest.TestCase):
    def test_high_drive_forms_resolved_channel_and_restart_breaks_it(self) -> None:
        base = SimulationConfig()
        # This regression intentionally follows regularised topology after
        # first contact; all such samples are explicitly diagnostic.
        forward = replace(
            base.forward,
            prediction_mode="diagnostic",
            radial_cells=32,
            axial_cells=56,
            integration_time_step_s=0.002,
            interface_thickness_cells=1.25,
            minimum_resolved_throat_cells=3,
            stable_channel_radius_m=0.001,
            stable_channel_hold_time_s=0.2,
        )
        upward = replace(
            base,
            backend=BackendMode.FORWARD,
            drive=DriveConfig(target_rpm=2000.0, ramp_duration_s=0.5),
            run=RunConfig(duration_s=2.0, output_interval_s=0.05),
            forward=forward,
        )
        formed = ForwardTopologyBackend().run(upward)
        self.assertEqual(float(np.max(formed.observables["target_touch"])), 1.0)
        self.assertEqual(float(np.max(formed.observables["finite_channel"])), 1.0)
        self.assertEqual(float(np.max(formed.observables["resolved_channel"])), 1.0)
        self.assertIn("finite_channel", {event.kind for event in formed.events})
        self.assertTrue(np.all(formed.observables["quantitative_valid_mask"] == 0.0))

        restart = ForwardState(
            float(formed.observables["burgers_C_m2_s"][-1]),
            formed.fields["final_phase_order_parameter"],
        )
        downward = replace(
            base,
            backend=BackendMode.FORWARD,
            drive=DriveConfig(
                initial_rpm=2000.0,
                target_rpm=0.0,
                ramp_duration_s=0.5,
            ),
            run=RunConfig(duration_s=2.0, output_interval_s=0.05),
            forward=forward,
        )
        broken = ForwardTopologyBackend().run(downward, initial_state=restart)
        self.assertEqual(float(broken.observables["finite_channel"][0]), 1.0)
        self.assertEqual(float(broken.observables["finite_channel"][-1]), 0.0)
        self.assertIn("channel_break", {event.kind for event in broken.events})


if __name__ == "__main__":
    unittest.main()
