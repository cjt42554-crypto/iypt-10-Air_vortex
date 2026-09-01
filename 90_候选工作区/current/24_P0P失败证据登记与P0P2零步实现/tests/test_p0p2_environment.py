from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path
from typing import Any

WORKSPACE = Path(__file__).resolve().parents[1]
if str(WORKSPACE) not in sys.path:
    sys.path.insert(0, str(WORKSPACE))

import p0p2_environment as environment


def _valid_snapshot() -> dict[str, Any]:
    return {
        "captured_monotonic_ns": 1,
        "online_logical_cpus": 32,
        "governors": {f"cpu{index}": "powersave" for index in range(32)},
        "ac_online": {"AC0": 1},
        "tctl_sources": [{"chip": "k10temp", "label": "Tctl", "temperature_c": 60.0}],
        "gpu": {
            "gpu": {
                "name": "NVIDIA GeForce RTX 5060 Laptop GPU",
                "uuid": "GPU-synthetic",
                "temperature.gpu": "45",
            },
            "compute_processes": [],
        },
    }


class EnvironmentEvaluationTests(unittest.TestCase):
    def test_valid_instantaneous_snapshot_passes(self) -> None:
        result = environment.evaluate_instantaneous_environment(_valid_snapshot())
        self.assertTrue(result["gate_pass"])
        self.assertTrue(all(result["gates"].values()))

    def test_hot_cpu_hot_gpu_and_other_gpu_process_fail(self) -> None:
        snapshot = _valid_snapshot()
        snapshot["tctl_sources"] = [
            {"chip": "k10temp", "label": "Tctl", "temperature_c": 91.0}
        ]
        snapshot["gpu"]["gpu"]["temperature.gpu"] = "76"
        snapshot["gpu"]["compute_processes"] = [
            {"pid": 9999, "process_name": "python", "used_gpu_memory_mib": 1.0}
        ]
        result = environment.evaluate_instantaneous_environment(snapshot)
        self.assertFalse(result["gate_pass"])
        self.assertFalse(result["gates"]["cpu_tctl_at_most_90_c"])
        self.assertFalse(result["gates"]["gpu_temperature_at_most_75_c"])
        self.assertFalse(result["gates"]["no_other_gpu_compute_process"])

    def test_missing_tctl_and_incomplete_governors_fail(self) -> None:
        snapshot = _valid_snapshot()
        snapshot["tctl_sources"] = []
        snapshot["governors"] = {"cpu0": "powersave"}
        result = environment.evaluate_instantaneous_environment(snapshot)
        self.assertFalse(result["gate_pass"])
        self.assertFalse(result["gates"]["reliable_tctl_available"])
        self.assertFalse(result["gates"]["governor_records_complete"])

    def test_cpu_delta_detects_background_compute(self) -> None:
        ticks = int(__import__("os").sysconf("SC_CLK_TCK"))
        start = {
            50: environment.ProcessSample(50, 100, 0, "python solver.py", "/python")
        }
        end = {
            50: environment.ProcessSample(50, 100, ticks, "python solver.py", "/python")
        }
        report = environment.cpu_delta_report(
            start, end, elapsed_s=1.0, excluded_pids=()
        )
        self.assertGreaterEqual(report["background_cpu_percent"], 100.0)
        self.assertEqual(len(report["other_compute_processes"]), 1)

    def test_full_gate_with_zero_duration_and_injected_idle_samples_passes(
        self,
    ) -> None:
        snapshot = _valid_snapshot()
        samples = {50: environment.ProcessSample(50, 100, 0, "desktop", "/desktop")}
        snapshots = [copy.deepcopy(snapshot), copy.deepcopy(snapshot)]
        process_snapshots = [samples, samples]
        report = environment.full_environment_gate(
            background_duration_s=0.0,
            sleep=lambda _seconds: None,
            snapshot_fn=lambda: snapshots.pop(0),
            process_snapshot_fn=lambda: process_snapshots.pop(0),
        )
        self.assertTrue(report["gate_pass"])
        self.assertTrue(all(report["background_gates"].values()))


if __name__ == "__main__":
    unittest.main()
