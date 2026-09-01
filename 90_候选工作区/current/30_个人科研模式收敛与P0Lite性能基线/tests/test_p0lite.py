"""High-value tests for the deliberately small P0-Lite harness."""

from __future__ import annotations

import copy
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import analyse_p0lite
import numpy as np
import p0lite_common
import run_p0lite


class P0LiteTests(unittest.TestCase):
    def _good_environment(self, commit: str) -> dict[str, object]:
        return {
            "captured_at_utc": "test",
            "online_logical_cpus": 32,
            "governors": {f"cpu{index}": "powersave" for index in range(32)},
            "ac_online": {"ACAD": 1},
            "cpu_tctl_c": 60.0,
            "gpu": {
                "available": True,
                "error": None,
                "gpus": [{"temperature.gpu": "50", "uuid": "GPU-test"}],
                "compute_processes": [],
            },
            "git_head": commit,
            "git_clean": True,
            "baseline_commit": commit,
        }

    def _dummy_attempt(self, root: Path, *, value: float = 1.0) -> Path:
        from airvortex.core import RunStatus, SimulationResult
        from airvortex.io import save_run_archive

        attempt = root / f"attempt-{value}-{len(list(root.iterdir()))}"
        attempt.mkdir()
        (attempt / "telemetry").mkdir()
        (attempt / "telemetry" / "runtime.jsonl").write_text("{}\n", encoding="utf-8")
        (attempt / "telemetry" / "pidstat.log").write_text("test\n", encoding="utf-8")
        p0lite_common.write_json(
            attempt / "telemetry" / "runtime_environment.json", {"gate_pass": True}
        )
        (attempt / "stdout.log").write_text("", encoding="utf-8")
        (attempt / "stderr.log").write_text("", encoding="utf-8")
        p0lite_common.write_json(attempt / "worker_timing.json", {"test_s": 1.0})
        p0lite_common.write_json(attempt / "memory.json", {"test": True})
        p0lite_common.write_json(attempt / "worker_identity.json", {"pid": 1})
        payload, _ = p0lite_common.effective_config("B1")
        p0lite_common.write_json(attempt / "effective_config.json", payload)
        from airvortex.core import SimulationConfig

        config = SimulationConfig.from_dict(payload)
        times = np.asarray(p0lite_common.EXPECTED_REGISTERED_TIMES, dtype=np.float64)
        result = SimulationResult(
            backend=config.backend,
            status=RunStatus.COMPLETED,
            time_s=times,
            observables={"test_value": np.full(times.shape, value)},
            metadata={
                "backend_id": "p0lite-test",
                "diagnostics": {
                    "completed_steps": 1,
                    "simulated_duration_s": 0.005,
                    "numerical_quality_gate": {"pass": True},
                    "pressure_accelerator": {"selected": "cuda", "fallback": False},
                },
            },
        )
        save_run_archive(attempt / "archive", config, result)
        numerical = p0lite_common.archive_numerical_payload(attempt / "archive")
        p0lite_common.write_json(attempt / "numerical_payload.json", numerical)
        gates = {
            "formal_config_roundtrip_exact": True,
            "result_completed": True,
            "numerical_quality_gate_pass": True,
            "cuda_selected": True,
            "cuda_no_fallback": True,
            "registered_times_exact": True,
            "archive_strict_reload": True,
            "canonical_payload_complete": True,
        }
        p0lite_common.write_json(
            attempt / "quality.json", {"evaluated": True, "gate_pass": True, "gates": gates}
        )
        p0lite_common.write_json(
            attempt / "run_manifest.json",
            {
                "slot": {"slot_id": "TIMED_R1_B1", "kind": "TIMED", "benchmark": "B1"},
                "environment_id": "test",
                "execution": {"end_to_end_wall_s": 1.0},
            },
        )
        p0lite_common.write_json(
            attempt / "status.json", {"status": "SUCCESS", "reason": None}
        )
        return attempt

    def test_schedule_has_exactly_sixteen_unique_slots(self) -> None:
        schedule = p0lite_common.load_schedule()
        identities = [slot["slot_id"] for slot in schedule["slots"]]
        self.assertEqual(len(identities), 16)
        self.assertEqual(len(set(identities)), 16)

    def test_all_benchmarks_formally_construct_and_change_only_duration(self) -> None:
        for benchmark, source_case in p0lite_common.BENCHMARK_SOURCES.items():
            payload, identity = p0lite_common.effective_config(benchmark)
            source = p0lite_common.strict_json(
                p0lite_common.SOURCE_CONFIG_ROOT / f"{source_case}.json"
            )
            expected = copy.deepcopy(source)
            expected["run"]["duration_s"] = 0.005
            self.assertEqual(payload, expected)
            self.assertEqual(identity["only_change"], "run.duration_s: 0.010 -> 0.005")

    def test_old_duration_shorter_than_ramp_is_formally_rejected(self) -> None:
        from airvortex.core import ConfigError, SimulationConfig

        payload, _ = p0lite_common.effective_config("B1")
        payload["run"]["duration_s"] = 0.002
        with self.assertRaises(ConfigError):
            SimulationConfig.from_dict(payload)

    def test_attempt_directories_never_overwrite_and_stop_at_three(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            attempts = [
                p0lite_common.new_attempt_directory(
                    "WARMUP_B1", commit="a" * 40, runs_root=root
                )
                for _ in range(3)
            ]
            self.assertEqual(len({path.name for path in attempts}), 3)
            with self.assertRaises(p0lite_common.P0LiteError):
                p0lite_common.new_attempt_directory(
                    "WARMUP_B1", commit="a" * 40, runs_root=root
                )

    def test_non_retryable_failure_stops_while_profiler_failure_can_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            attempt = root / "PROFILE_CUDA_B2" / "attempt"
            attempt.mkdir(parents=True)
            p0lite_common.write_json(
                attempt / "status.json", {"status": "FAILED", "reason": "CUDA_FALLBACK: no"}
            )
            with self.assertRaises(p0lite_common.P0LiteError):
                run_p0lite._has_success("PROFILE_CUDA_B2", root)
            p0lite_common.write_json(
                attempt / "status.json",
                {"status": "FAILED", "reason": "PROFILER_FAILURE: tool stopped"},
            )
            self.assertFalse(run_p0lite._has_success("PROFILE_CUDA_B2", root))

    def test_environment_gate_and_invalid_environment_never_launch_worker(self) -> None:
        commit = "b" * 40
        good = self._good_environment(commit)
        self.assertTrue(
            p0lite_common.evaluate_environment(
                good, background_cpu_percent=[10.0] * 30
            )["gate_pass"]
        )
        invalid = dict(good)
        invalid["cpu_tctl_c"] = 96.0
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(
            run_p0lite, "_worker_command", side_effect=AssertionError("worker launched")
        ) as launcher:
            status, attempt = run_p0lite.execute_slot(
                "TIMED_R1_B1",
                baseline_commit=commit,
                runs_root=Path(temporary),
                environment_override=invalid,
                background_cpu_override=[10.0] * 30,
            )
            self.assertEqual(status, "INVALID_ENVIRONMENT")
            self.assertTrue((attempt / "archive").is_dir())
            launcher.assert_not_called()

    def test_runtime_pollution_detects_external_cpu_group_and_gpu_process(self) -> None:
        processes = [{"cpu_percent": 60.0} for _ in range(4)]
        reasons = run_p0lite._instant_pollution_reasons(
            processes, {"compute_processes": []}, 32
        )
        self.assertIn(
            "four_or_more_external_processes_each_use_at_least_half_a_core", reasons
        )
        reasons = run_p0lite._instant_pollution_reasons(
            [], {"compute_processes": [{"is_target_worker": False}]}, 32
        )
        self.assertIn("other_gpu_compute_process_detected", reasons)

    def test_config_probe_uses_a_fresh_process(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, _ = p0lite_common.effective_config("B1")
            p0lite_common.write_json(root / "config.json", config)
            completed = subprocess.run(
                [
                    str(p0lite_common.GPU_PYTHON),
                    str(p0lite_common.HERE / "p0lite_worker.py"),
                    "--mode",
                    "config-probe",
                    "--config",
                    str(root / "config.json"),
                    "--output",
                    str(root / "result.json"),
                ],
                cwd=p0lite_common.HERE,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = p0lite_common.strict_json(root / "result.json")
            self.assertNotEqual(result["pid"], os.getpid())
            self.assertTrue(result["roundtrip_exact"])

    def test_success_requires_complete_quality_cuda_and_strict_archive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            attempt = self._dummy_attempt(Path(temporary))
            self.assertEqual(
                p0lite_common.validate_success_artifacts(attempt)["status"], "completed"
            )
            quality = p0lite_common.strict_json(attempt / "quality.json")
            quality["gates"]["cuda_no_fallback"] = False
            p0lite_common.write_json(attempt / "quality.json", quality)
            with self.assertRaises(p0lite_common.P0LiteError):
                p0lite_common.validate_success_artifacts(attempt)

    def test_damaged_archive_manifest_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            attempt = self._dummy_attempt(Path(temporary))
            manifest = p0lite_common.strict_json(attempt / "archive" / "manifest.json")
            manifest["files"]["result.json"]["sha256"] = "0" * 64
            p0lite_common.write_json(attempt / "archive" / "manifest.json", manifest)
            with self.assertRaises(p0lite_common.P0LiteError):
                p0lite_common.validate_success_artifacts(attempt)

    def test_canonical_payload_detects_exact_equality_and_difference(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = self._dummy_attempt(root, value=1.0)
            second = self._dummy_attempt(root, value=1.0)
            third = self._dummy_attempt(root, value=2.0)
            payloads = [
                p0lite_common.strict_json(path / "numerical_payload.json")
                for path in (first, second)
            ]
            self.assertTrue(p0lite_common.compare_payloads(payloads)["gate_pass"])
            payloads.append(p0lite_common.strict_json(third / "numerical_payload.json"))
            self.assertFalse(p0lite_common.compare_payloads(payloads)["gate_pass"])

    def test_analyser_interprets_leftover_running_as_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            attempt = Path(temporary) / "attempt"
            attempt.mkdir()
            p0lite_common.write_json(
                attempt / "status.json", {"status": "RUNNING", "reason": None}
            )
            p0lite_common.write_json(
                attempt / "run_manifest.json",
                {
                    "slot": {
                        "slot_id": "WARMUP_B1",
                        "kind": "WARMUP",
                        "benchmark": "B1",
                    }
                },
            )
            record = analyse_p0lite.inspect_attempt(attempt)
            self.assertEqual(record["effective_status"], "INTERRUPTED")
            self.assertEqual(record["stored_status"], "RUNNING")

    def test_timed_and_both_profile_modes_share_identical_benchmark_config(self) -> None:
        schedule = p0lite_common.load_schedule()["slots"]
        for benchmark in ("B2", "B3"):
            hashes = []
            for slot in schedule:
                if slot["benchmark"] == benchmark:
                    _, identity = p0lite_common.effective_config(benchmark)
                    hashes.append(identity["effective_sha256"])
            self.assertGreaterEqual(len(hashes), 5)
            self.assertEqual(len(set(hashes)), 1)

    def test_nsys_minimal_csv_parser_extracts_kernel_api_and_memcpy(self) -> None:
        fixtures = {
            "cuda_api_sum": "Time (%),Total Time (ns),Num Calls,Avg (ns),Med (ns),Min (ns),Max (ns),StdDev (ns),Name\n100,10,2,5,5,5,5,0,cudaMalloc\n",
            "cuda_gpu_kern_sum": "Time (%),Total Time (ns),Instances,Avg (ns),Med (ns),Min (ns),Max (ns),StdDev (ns),Name\n100,20,1,20,20,20,20,0,kernel\n",
            "cuda_gpu_mem_time_sum": "Time (%),Total Time (ns),Count,Avg (ns),Med (ns),Min (ns),Max (ns),StdDev (ns),Operation\n50,30,1,30,30,30,30,0,[CUDA memcpy Host-to-Device]\n50,40,1,40,40,40,40,0,[CUDA memcpy Device-to-Host]\n",
            "cuda_gpu_mem_size_sum": "Total (MB),Count,Avg (MB),Med (MB),Min (MB),Max (MB),StdDev (MB),Operation\n0.001,1,0.001,0.001,0.001,0.001,0,[CUDA memcpy Host-to-Device]\n0.002,1,0.002,0.002,0.002,0.002,0,[CUDA memcpy Device-to-Host]\n",
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = {}
            for name, payload in fixtures.items():
                path = root / f"{name}.csv"
                path.write_text(payload, encoding="utf-8")
                paths[name] = path
            summary = p0lite_common.parse_nsys_csvs(paths)
            self.assertEqual(summary["allocation_api_calls"], 2)
            self.assertEqual(summary["h2d"]["total_bytes"], 1000)
            self.assertEqual(summary["d2h"]["total_bytes"], 2000)

    def test_worker_exception_and_explicit_interrupt_status_retain_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "bad.json").write_text("{}\n", encoding="utf-8")
            completed = subprocess.run(
                [
                    str(p0lite_common.GPU_PYTHON),
                    str(p0lite_common.HERE / "p0lite_worker.py"),
                    "--mode",
                    "config-probe",
                    "--config",
                    str(root / "bad.json"),
                    "--output",
                    str(root / "failure.json"),
                ],
                cwd=p0lite_common.HERE,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(completed.returncode, 0)
            failure = p0lite_common.strict_json(root / "failure.json")
            self.assertEqual(failure["category"], "CONFIG_IDENTITY_FAILURE")
            attempt = root / "attempt"
            attempt.mkdir()
            p0lite_common.write_status(attempt, "INTERRUPTED", "test interrupt")
            self.assertEqual(
                p0lite_common.strict_json(attempt / "status.json")["status"],
                "INTERRUPTED",
            )


if __name__ == "__main__":
    unittest.main()
