from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = HERE.parents[2]
FORMAL_SOURCE = REPOSITORY_ROOT / "02_模拟程序" / "src"
if str(FORMAL_SOURCE) not in sys.path:
    sys.path.insert(0, str(FORMAL_SOURCE))
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from airvortex.backends.high_fidelity_3d import HighFidelityBackend
from airvortex.io.config_io import load_config_json
from airvortex.numerics.cartesian3d import ImmersedCartesianGrid
from analyse_d1_dynamic_shadow import (
    classify_materiality,
    compute_observable_metrics,
)
from controlled_shadow_grid import (
    REBUILT_FIELDS,
    ShadowGridError,
    build_bound_nominal_g1_mask,
    compare_rebuilt_fields,
    install_shadow_bar_mask,
    snapshot_rebuilt_fields,
)
from run_d1_dynamic_shadow import (
    LEVEL_CONFIGS,
    _arrays_identity,
    _full_window_config_identity,
    _limiter_for_pair,
    _state_arrays,
)


def make_grid() -> ImmersedCartesianGrid:
    return ImmersedCartesianGrid(
        60,
        60,
        54,
        0.045,
        0.08,
        bar_half_length_m=0.015,
        bar_radius_m=0.003,
        bar_centre_z_m=0.0045,
    )


class ControlledShadowGridTests(unittest.TestCase):
    def test_perturb_then_restore_rebuilds_every_registered_field(self) -> None:
        formal = make_grid()
        rebuilt = make_grid()
        expected = snapshot_rebuilt_fields(formal)
        original = rebuilt.bar_mask.copy()
        perturbed = original.copy()
        fluid_index = np.argwhere(rebuilt.vessel_mask & ~original)[0]
        perturbed[tuple(fluid_index)] = True
        perturb = install_shadow_bar_mask(rebuilt, perturbed)
        self.assertFalse(perturb["installer_has_g0_noop_special_case"])
        self.assertFalse(np.array_equal(rebuilt.bar_mask, original))
        restored = install_shadow_bar_mask(rebuilt, original)
        self.assertFalse(restored["installer_has_g0_noop_special_case"])
        comparison = compare_rebuilt_fields(expected, rebuilt)
        self.assertTrue(comparison["pass"])
        self.assertEqual(set(comparison["fields"]), set(REBUILT_FIELDS))

    def test_g0_solid_to_candidate_fluid_cell_gets_analytic_geometry(self) -> None:
        grid = make_grid()
        candidate = grid.bar_mask.copy()
        removed = tuple(int(value) for value in np.argwhere(candidate)[0])
        candidate[removed] = False
        install_shadow_bar_mask(grid, candidate)
        self.assertTrue(grid.active_mask[removed])
        self.assertGreaterEqual(grid.index[removed], 0)
        self.assertNotEqual(grid.nearest_solid_distance_m[removed], 0.0)
        normal = np.asarray(
            [
                grid.wall_normal_x[removed],
                grid.wall_normal_y[removed],
                grid.wall_normal_z[removed],
            ]
        )
        self.assertTrue(np.isfinite(normal).all())
        self.assertGreater(np.linalg.norm(normal), 0.0)

    def test_candidate_mask_dtype_and_shape_fail_closed(self) -> None:
        grid = make_grid()
        with self.assertRaises(ShadowGridError):
            install_shadow_bar_mask(grid, grid.bar_mask.astype(np.uint8))
        with self.assertRaises(ShadowGridError):
            install_shadow_bar_mask(grid, np.zeros((2, 2, 2), dtype=bool))

    def test_rebuilt_g0_initial_state_is_bitwise_identical(self) -> None:
        config = load_config_json(LEVEL_CONFIGS[4])
        backend = HighFidelityBackend()
        formal = backend.make_grid(config)
        rebuilt = backend.make_grid(config)
        install_shadow_bar_mask(rebuilt, rebuilt.bar_mask.copy())
        identity = _arrays_identity(
            _state_arrays(backend.initialise_state(config, formal)),
            _state_arrays(backend.initialise_state(config, rebuilt)),
        )
        self.assertTrue(identity["pass"])

    def test_nominal_g1_rebuild_matches_static_four_cell_binding(self) -> None:
        binding = build_bound_nominal_g1_mask(make_grid())
        self.assertEqual(binding.cells_per_bar_diameter, 4.0)
        self.assertEqual(binding.expected_bar_cell_count, 236)
        self.assertEqual(int(np.sum(binding.mask)), 236)
        self.assertEqual(
            binding.expected_mask_sha256,
            "fec79a655f37feab7e947d28a762409c6002afc954f54ea0f3bb8afba6b574f2",
        )


class LockstepProtocolTests(unittest.TestCase):
    def test_pair_limiter_uses_output_then_stricter_variant(self) -> None:
        self.assertEqual(
            _limiter_for_pair(
                5.0e-6, "registered_time_step", 4.0e-6, "capillary_stability", 3.0e-6
            ),
            "output_alignment",
        )
        self.assertEqual(
            _limiter_for_pair(
                5.0e-6, "registered_time_step", 4.0e-6, "capillary_stability", 8.0e-6
            ),
            "capillary_stability",
        )

    def test_full_window_config_changes_only_duration(self) -> None:
        for level in (6, 9):
            identity = _full_window_config_identity(level)
            self.assertTrue(identity["gate_pass"])
            self.assertTrue(identity["gates"]["only_duration_override"])
            self.assertTrue(identity["gates"]["required_values_unchanged"])


class MaterialityAnalysisTests(unittest.TestCase):
    def test_metric_definitions_use_initial_value_removed_trajectories(self) -> None:
        metrics = compute_observable_metrics(
            np.asarray([10.0, 11.0, 12.0]),
            np.asarray([20.0, 22.0, 24.0]),
            np.asarray([30.0, 31.5, 33.0]),
            np.asarray([40.0, 42.5, 45.0]),
            1.0e-12,
        )
        self.assertAlmostEqual(metrics["N_6"], np.sqrt(5.0 / 3.0))
        self.assertAlmostEqual(metrics["E_0"], np.sqrt(1.25 / 3.0))
        self.assertGreater(metrics["rho"], 0.0)

    def test_four_frozen_classification_branches(self) -> None:
        thresholds = {
            "direct_relative_l2_material": 0.05,
            "eta_material": 0.5,
            "rho_reduction_max": 0.5,
            "rho_increase_min": 1.5,
            "material_primary_observable_count_min": 2,
            "small_eta_max": 0.1,
            "small_rho_interval": [0.8, 1.25],
        }

        def item(d: float, eta: float, rho: float) -> dict[str, float]:
            return {
                "D_6_relative_L2": d,
                "D_9_relative_L2": d,
                "eta_6": eta,
                "eta_9": eta,
                "rho": rho,
            }

        reduction = {
            "a": item(0.1, 0.8, 0.4),
            "b": item(0.1, 0.8, 0.4),
            "c": item(0.02, 0.05, 1.0),
            "d": item(0.02, 0.05, 1.0),
        }
        self.assertEqual(
            classify_materiality(reduction, thresholds)[0],
            "GEOMETRY_CONTRIBUTOR_WITH_REDUCTION_SIGNAL",
        )
        increase = {name: item(0.1, 0.8, 1.6) for name in ("a", "b", "c", "d")}
        self.assertEqual(
            classify_materiality(increase, thresholds)[0],
            "GEOMETRY_CONTRIBUTOR_SIGNAL_WITHOUT_REDUCTION",
        )
        small = {name: item(0.01, 0.05, 1.0) for name in ("a", "b", "c", "d")}
        self.assertEqual(
            classify_materiality(small, thresholds)[0],
            "GEOMETRY_EFFECT_SMALL_IN_REGISTERED_WINDOW",
        )
        mixed = {
            "a": item(0.1, 0.8, 1.0),
            "b": item(0.01, 0.05, 1.0),
            "c": item(0.01, 0.05, 1.0),
            "d": item(0.01, 0.05, 1.0),
        }
        self.assertEqual(
            classify_materiality(mixed, thresholds)[0],
            "MIXED_OR_INDETERMINATE_GEOMETRY_EFFECT",
        )


if __name__ == "__main__":
    unittest.main()
