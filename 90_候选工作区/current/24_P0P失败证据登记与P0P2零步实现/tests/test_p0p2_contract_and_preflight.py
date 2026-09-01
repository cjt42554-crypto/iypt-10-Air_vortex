from __future__ import annotations

import copy
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

WORKSPACE = Path(__file__).resolve().parents[1]
if str(WORKSPACE) not in sys.path:
    sys.path.insert(0, str(WORKSPACE))

import p0p2_contract as contract
import run_p0p2_preflight as preflight

if str(contract.FORMAL_SOURCE) not in sys.path:
    sys.path.insert(0, str(contract.FORMAL_SOURCE))

from airvortex.core import ConfigError, SimulationConfig


def _fake_probe(benchmark: str, *, passed: bool = True) -> dict[str, Any]:
    index = tuple(contract.BENCHMARKS).index(benchmark) + 1
    report = {
        "status": (
            "HF3D_P0P2_EXACT_CASE_ZERO_STEP_PROBE_PASS"
            if passed
            else "HF3D_P0P2_EXACT_CASE_ZERO_STEP_PROBE_FAILED_STOPPED"
        ),
        "gate_pass": passed,
        "benchmark_id": benchmark,
        "process_identity": {"pid": 1000 + index, "process_start_ticks": 2000 + index},
        "hf3d_time_steps_advanced": 0,
    }
    return {
        "benchmark_id": benchmark,
        "returncode": 0 if passed else 2,
        "stderr_empty": True,
        "stderr_utf8": "",
        "report": report,
        "gate_pass": passed,
    }


def _passing_environment(**_kwargs: Any) -> dict[str, Any]:
    return {"gate_pass": True, "stage": "synthetic_pass"}


class EffectiveConfigTests(unittest.TestCase):
    def test_v1_duration_ramp_conflict_is_formal_config_error(self) -> None:
        payload = contract.strict_json(contract.source_config_path("B1"))
        self.assertIsInstance(payload, dict)
        payload["run"]["duration_s"] = 0.002
        with self.assertRaisesRegex(
            ConfigError, "drive ramp duration cannot exceed run duration"
        ):
            SimulationConfig.from_dict(payload)

    def test_all_p0p2_configs_formally_round_trip(self) -> None:
        for benchmark in contract.BENCHMARKS:
            with self.subTest(benchmark=benchmark):
                payload, identity = contract.effective_config(benchmark)
                parsed = SimulationConfig.from_dict(payload)
                self.assertEqual(parsed.to_dict(), payload)
                self.assertEqual(parsed.run.duration_s, 0.005)
                self.assertEqual(parsed.drive.ramp_duration_s, 0.005)
                self.assertEqual(
                    identity["effective_canonical_sha256"],
                    contract.BENCHMARKS[benchmark]["effective_config_sha256"],
                )

    def test_extra_effective_field_is_rejected(self) -> None:
        payload, _identity = contract.effective_config("B1")
        payload["run"]["unauthorized_extra_field"] = 1
        with self.assertRaisesRegex(contract.P0P2Error, "changed extra fields"):
            contract.validate_effective_payload("B1", payload)

    def test_effective_canonical_hash_drift_is_rejected(self) -> None:
        definitions = copy.deepcopy(contract.BENCHMARKS)
        definitions["B1"]["effective_config_sha256"] = "0" * 64
        with (
            mock.patch.object(contract, "BENCHMARKS", definitions),
            self.assertRaisesRegex(
                contract.P0P2Error, "effective configuration canonical hash drifted"
            ),
        ):
            contract.effective_config("B1")

    def test_source_file_hash_drift_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            changed = Path(temporary) / "changed.json"
            changed.write_text("{}\n", encoding="utf-8")
            with (
                mock.patch.object(contract, "source_config_path", return_value=changed),
                self.assertRaisesRegex(
                    contract.P0P2Error, "source configuration hash drifted"
                ),
            ):
                contract.effective_config("B1")

    def test_generated_outputs_are_byte_deterministic(self) -> None:
        first = contract.generated_outputs()
        second = contract.generated_outputs()
        self.assertEqual(first, second)
        self.assertTrue(contract.verify_generated_outputs()["gate_pass"])


class IdentitySeparationTests(unittest.TestCase):
    def test_schedule_and_all_new_namespaces_are_distinct(self) -> None:
        schedule = contract.fixed_schedule()
        self.assertEqual(len(schedule["ordered_items"]), 18)
        self.assertEqual(len(set(contract.schedule_sample_ids())), 18)
        self.assertTrue(all(contract.assert_namespace_separation().values()))
        self.assertTrue(
            all(
                item["sample_id"].startswith("P0P2_")
                for item in schedule["ordered_items"]
            )
        )

    def test_v1_and_e0_runtime_inputs_are_rejected(self) -> None:
        prohibited = (
            contract.V1_RAW_ROOT,
            contract.V1_RAW_ROOT / "execution_claim.json",
            contract.V1_RAW_ROOT / "schedule_ledger.json",
            contract.V1_RAW_ROOT / "WARMUP_B1",
            contract.E0_SNAPSHOT_ROOT,
            contract.E0_SNAPSHOT_ROOT / "schedule_ledger.json",
        )
        for path in prohibited:
            with (
                self.subTest(path=path),
                self.assertRaisesRegex(
                    contract.P0P2Error, "prohibited V1/E0 runtime input"
                ),
            ):
                contract.assert_runtime_input_separation([path])

    def test_new_runtime_inputs_are_accepted(self) -> None:
        gates = contract.assert_runtime_input_separation(
            [
                contract.CONTRACT_PATH,
                contract.AUTHORIZATION_PATH,
                contract.source_config_path("B1"),
                contract.EFFECTIVE_CONFIG_ROOT / "P0P2_B1.json",
                contract.FORMAL_SOURCE,
            ]
        )
        self.assertTrue(all(gates.values()))

    def test_copied_v1_ledger_byte_identity_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            copied = Path(temporary) / "renamed_new_ledger.json"
            copied.write_bytes(
                (contract.V1_RAW_ROOT / "schedule_ledger.json").read_bytes()
            )
            with self.assertRaisesRegex(
                contract.P0P2Error, "byte identity used as runtime input"
            ):
                contract.assert_runtime_input_separation([copied])

    def test_new_raw_root_overlap_with_v1_is_rejected(self) -> None:
        with mock.patch.object(contract, "RAW_ROOT", contract.V1_RAW_ROOT / "nested"):
            self.assertFalse(
                contract.assert_namespace_separation()["raw_roots_disjoint"]
            )


class UnifiedPreflightTests(unittest.TestCase):
    def test_exact_denial_keeps_only_runtime_gate_false(self) -> None:
        before = preflight.side_effect_state()
        report = preflight.run_preflight(
            background_duration_s=0.0,
            probe_runner=_fake_probe,
            environment_runner=_passing_environment,
        )
        self.assertEqual(
            report["status"],
            "HF3D_P0P2_ZERO_STEP_STATIC_PASS_DYNAMIC_EXECUTION_DENIED",
        )
        self.assertFalse(report["gate_pass"])
        self.assertEqual(
            [key for key, value in report.items() if isinstance(value, bool)],
            ["gate_pass"],
        )
        self.assertTrue(report["static_qualification"]["gate_pass"])
        self.assertFalse(report["component_gates"]["authorization"])
        self.assertTrue(report["component_gates"]["environment"])
        self.assertEqual(report["hf3d_time_steps_advanced"], 0)
        self.assertEqual(before, preflight.side_effect_state())

    def test_one_invalid_benchmark_fails_whole_schedule_before_environment(
        self,
    ) -> None:
        calls: list[str] = []

        def runner(benchmark: str) -> dict[str, Any]:
            calls.append(benchmark)
            return _fake_probe(benchmark, passed=benchmark != "B2")

        def forbidden_environment(**_kwargs: Any) -> dict[str, Any]:
            raise AssertionError("environment must not run after a failed probe")

        report = preflight.run_preflight(
            background_duration_s=0.0,
            probe_runner=runner,
            environment_runner=forbidden_environment,
        )
        self.assertEqual(calls, ["B1", "B2"])
        self.assertFalse(report["gate_pass"])
        self.assertFalse(report["static_qualification"]["gate_pass"])
        self.assertEqual(
            report["environment"]["stage"], "not_run_due_to_zero_step_probe_failure"
        )
        self.assertEqual(report["hf3d_time_steps_advanced"], 0)
        self.assertFalse(contract.RAW_ROOT.exists())
        self.assertFalse(contract.LOCK_PATH.exists())

    def test_environment_failure_is_part_of_the_unified_failure(self) -> None:
        report = preflight.run_preflight(
            background_duration_s=0.0,
            probe_runner=_fake_probe,
            environment_runner=lambda **_kwargs: {
                "gate_pass": False,
                "stage": "synthetic_environment_rejection",
            },
        )
        self.assertFalse(report["gate_pass"])
        self.assertFalse(report["static_qualification"]["gate_pass"])
        self.assertFalse(report["component_gates"]["environment"])

    def test_detected_preflight_side_effect_fails_closed(self) -> None:
        absent = preflight.side_effect_state()
        changed = copy.deepcopy(absent)
        changed["claim_path"] = {
            "exists": True,
            "is_symlink": False,
            "kind": "file",
            "byte_length": 1,
        }
        with mock.patch.object(
            preflight, "side_effect_state", side_effect=[absent, changed]
        ):
            report = preflight.run_preflight(
                background_duration_s=0.0,
                probe_runner=_fake_probe,
                environment_runner=_passing_environment,
            )
        self.assertFalse(report["gate_pass"])
        self.assertFalse(report["static_qualification"]["gate_pass"])
        self.assertFalse(report["component_gates"]["preflight_side_effect_free"])


if __name__ == "__main__":
    unittest.main()
