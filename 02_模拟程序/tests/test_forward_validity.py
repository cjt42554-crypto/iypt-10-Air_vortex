"""10-A/B4: exercise actual CP evolution, archives, GUI and scientific consumers."""
from __future__ import annotations

from contextlib import redirect_stdout, redirect_stderr
from dataclasses import replace
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from airvortex import cli
from airvortex.analysis import aggregate_phase_data
from airvortex.backends.forward_topology import ForwardTopologyBackend, ForwardState, actual_bar_omega_rad_s
from airvortex.core import ConfigError, Control, GasModelConfig, RunStatus, SimulationConfig, quantitative_valid_mask
from airvortex.gui.model import normalize_simulation_result
from airvortex.io import load_run_archive, save_config_json
from airvortex.numerics.variational_interface import (
    VariationalStepError, correct_liquid_volume, initialise_flat_order_parameter,
)
from airvortex.runtime import run_once, run_sweep
from test_forward_topology import compact_config, geometry_for
from validity_fixtures import ideal_test_reservoir


def conditional_config(*, output=0.01, step=0.001, duration=0.04, depth=0.01, until=1.0):
    c = compact_config(rpm=800.0, duration_s=duration, output_s=output)
    return replace(c, gas=ideal_test_reservoir(until_s=until, max_depth_m=depth),
                   forward=replace(c.forward, integration_time_step_s=step,
                                   initial_circulation_m2_s=c.forward.circulation_coupling_m2 * actual_bar_omega_rad_s(c, 0.0)))


class CPValidityTests(unittest.TestCase):
    def test_default_missing_evidence_stops_before_advancing(self):
        r = ForwardTopologyBackend().run(replace(compact_config(), gas=GasModelConfig()))
        self.assertEqual(r.metadata["quantitative_validity"]["status"], "physical_exit")
        self.assertEqual(r.metadata["quantitative_validity"]["reason"], "GAS_PRESSURE_EVIDENCE_MISSING")
        self.assertEqual(r.metadata["discretisation"]["accepted_internal_steps"], 0)
        self.assertEqual(r.time_s.tolist(), [0.0])
        self.assertFalse(quantitative_valid_mask(r).any())
        self.assertIsNone(r.metadata["gas_pressure_diagnostics"][0]["epsilon_g"])

    def test_formal_cli_archive_and_gui_share_conditional_validity(self):
        c = conditional_config()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            save_config_json(root / "config.json", c)
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                code = cli.main(["run", "--backend", "forward", "--config", str(root / "config.json"), "--output", str(root / "run")])
            self.assertEqual(code, 0)
            r = load_run_archive(root / "run").result
            self.assertTrue(quantitative_valid_mask(r).all())
            self.assertGreater(r.metadata["discretisation"]["accepted_internal_steps"], 1)
            self.assertGreater(r.observables["surface_depth_m"][-1], 0.0)
            self.assertFalse(r.metadata["quantitative_validity"]["apparatus_quantitative_prediction_qualified"])
            view = normalize_simulation_result(r, c.backend)
            self.assertEqual(view.quantitative_validity, r.metadata["quantitative_validity"])
            self.assertEqual(tuple(quantitative_valid_mask(r)), view.quantitative_valid_mask)
            data = (root / "run" / "timeseries.csv").read_text()
            self.assertIn("quantitative_valid_mask", data.splitlines()[0])

    def test_internal_time_domain_exit_is_independent_of_output_interval(self):
        expiry = 0.0073
        for output, step in ((0.01, 0.001), (0.0025, 0.001), (0.01, 0.0005), (0.0025, 0.0005)):
            with self.subTest(output=output, step=step):
                c = conditional_config(output=output, step=step, until=expiry)
                r = ForwardTopologyBackend().run(c)
                v = r.metadata["quantitative_validity"]
                lo, hi = v["termination_interval_s"]
                self.assertLessEqual(lo, expiry)
                self.assertGreater(hi, expiry)
                self.assertLessEqual(hi - lo, c.forward.validity_event_tolerance_s)
                self.assertEqual(v["reason"], "GAS_PRESSURE_EVIDENCE_TIME_DOMAIN_EXCEEDED")
                self.assertEqual(r.time_s[-1], hi)
                self.assertFalse(quantitative_valid_mask(r)[-1])
                self.assertIn("validity_last_confirmed_phase", r.fields)

    def test_evolving_geometry_exit_has_bounded_time_discretisation_change(self):
        brackets = []
        for output, step in ((0.01, 0.001), (0.0025, 0.001), (0.01, 0.0005), (0.0025, 0.0005), (0.0025, 0.00025)):
            r = ForwardTopologyBackend().run(conditional_config(output=output, step=step, depth=0.001))
            v = r.metadata["quantitative_validity"]
            self.assertEqual(v["reason"], "OPEN_RESERVOIR_GEOMETRIC_EVIDENCE_DOMAIN_EXCEEDED")
            brackets.append(v["termination_interval_s"])
            self.assertGreater(r.observables["surface_depth_m"][-1], 0.001)
        # This is a numerical event-time comparison, not a 1e-5 s physical
        # root claim: varying the integrator changes the discrete trajectory.
        midpoints = np.mean(brackets, axis=1)
        self.assertLess(np.ptp(midpoints), 5.0e-5)
        self.assertLess(abs(midpoints[2] - midpoints[4]), abs(midpoints[0] - midpoints[4]))

    def test_diagnostic_tail_never_regains_validity_or_enters_scan(self):
        c = conditional_config(until=0.0073)
        c = replace(c, forward=replace(c.forward, prediction_mode="diagnostic"))
        r = ForwardTopologyBackend().run(c)
        mask = quantitative_valid_mask(r)
        self.assertTrue(mask[0])
        self.assertFalse(mask[-1])
        self.assertTrue(np.all(np.diff(mask.astype(int)) <= 0))
        self.assertEqual(r.time_s[-1], c.run.duration_s)
        self.assertEqual(r.metadata["quantitative_validity"]["status"], "diagnostic_continuation")
        with tempfile.TemporaryDirectory() as directory:
            outcome = run_sweep(c, "drive.target_rpm", [800.0], Path(directory) / "sweep")
            manifest = json.loads((outcome.output / "sweep_manifest.json").read_text())
            self.assertTrue(manifest["cases"][0]["out_of_model_domain"])
            self.assertNotEqual(manifest["cases"][0]["scientific_completion_class"], "completed_full")

    def test_numerical_failure_and_user_stop_remain_distinct(self):
        c = conditional_config()
        with patch("airvortex.backends.forward_topology.advance_variational_phase", side_effect=VariationalStepError("controlled numerical failure")):
            with tempfile.TemporaryDirectory() as directory:
                outcome = run_once(c, Path(directory) / "failed")
                self.assertFalse(outcome.succeeded)
                r = load_run_archive(outcome.output).result
        self.assertEqual(r.status, RunStatus.FAILED)
        self.assertEqual(r.metadata["quantitative_validity"]["status"], "numerical_failure")
        self.assertFalse(quantitative_valid_mask(r).any())
        control = Control()
        control.request_stop("test user stop")
        r = ForwardTopologyBackend().run(c, control)
        self.assertEqual(r.status, RunStatus.STOPPED)
        self.assertEqual(r.metadata["quantitative_validity"]["status"], "user_stopped")

    def test_unknown_model_and_illegal_input_are_rejected_without_results(self):
        raw = conditional_config().to_dict()
        raw["forward"]["model_branch"] = "AV-FWD-UNIMPLEMENTED"
        with self.assertRaises(ConfigError):
            SimulationConfig.from_dict(raw)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "invalid.json").write_text(json.dumps(raw))
            with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
                code = cli.main(["run", "--backend", "forward", "--config", str(root / "invalid.json"), "--output", str(root / "out")])
            self.assertEqual(code, 2)
            self.assertFalse((root / "out" / "arrays.npz").exists())

    def test_initial_closed_gas_invalidates_entire_interface(self):
        c = conditional_config()
        _, geometry, epsilon = geometry_for(c)
        phase = initialise_flat_order_parameter(geometry, epsilon)
        phase[2:4, 10:12] = -1.0
        phase = correct_liquid_volume(phase, geometry, geometry.target_liquid_volume_m3,
                                     relative_tolerance=c.forward.volume_relative_tolerance)
        r = ForwardTopologyBackend().run(c, initial_state=ForwardState(0.0, phase))
        self.assertGreater(r.observables["isolated_gas_volume_m3"][0], 0.0)
        self.assertEqual(r.metadata["quantitative_validity"]["reason"], "ISOLATED_GAS_COMPONENT_WHOLE_INTERFACE_INVALID")
        self.assertFalse(quantitative_valid_mask(r).any())
        self.assertEqual(r.metadata["discretisation"]["accepted_internal_steps"], 0)


if __name__ == "__main__":
    unittest.main()
