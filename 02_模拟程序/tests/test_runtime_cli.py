from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
import hashlib
import io
import json
from pathlib import Path
import platform
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import numpy as np

from airvortex import cli
from airvortex.core import (
    BackendMode,
    DriveConfig,
    Event,
    Progress,
    RunConfig,
    RunStatus,
    SimulationConfig,
    SimulationResult,
)
from airvortex.io import (
    load_run_archive,
    save_config_json,
    save_run_archive,
    source_tree_identity,
)
from airvortex.runtime import (
    BackendRegistry,
    BackendRegistryError,
    InvalidStateTransition,
    JobController,
    JobState,
    compare_archives,
    discover_backends,
    linear_values,
    logarithmic_values,
    run_once,
    run_paired_compare,
    run_sweep,
)


def _result(status: RunStatus = RunStatus.COMPLETED, samples: int = 2) -> SimulationResult:
    time_s = np.linspace(0.0, 0.01, samples)
    return SimulationResult(
        backend=BackendMode.FORWARD,
        time_s=time_s,
        observables={"surface_depth_m": np.zeros(samples)},
        status=status,
    )


class _FakeBackend:
    mode = BackendMode.FORWARD

    def run(self, config, control=None, progress=None):
        del config, control, progress
        return _result()


from validity_fixtures import ideal_test_reservoir, synthetic_signal_result


class BackendRegistryTests(unittest.TestCase):
    def test_register_alias_create_and_reject_duplicate(self) -> None:
        registry = BackendRegistry()
        registry.register("forward", _FakeBackend, aliases=("f",))
        self.assertEqual(registry.ids(), ("forward",))
        self.assertIsInstance(registry.create("f"), _FakeBackend)
        with self.assertRaises(BackendRegistryError):
            registry.register("forward", _FakeBackend)
        with self.assertRaises(BackendRegistryError):
            registry.create("missing")

    def test_builtin_discovery_constructs_both_backends(self) -> None:
        registry = discover_backends(probe=True)
        self.assertEqual(registry.ids(), ("forward", "high_fidelity"))
        self.assertFalse(registry.discovery_errors)
        self.assertEqual(registry.create("forward").mode, BackendMode.FORWARD)
        self.assertEqual(
            registry.create("hf").mode,
            BackendMode.HIGH_FIDELITY,
        )


class JobControllerTests(unittest.TestCase):
    @staticmethod
    def _wait_until(predicate, timeout: float = 2.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.002)
        return bool(predicate())

    def test_pause_acknowledges_checkpoint_stops_progress_then_resumes(self) -> None:
        counter = 0
        counter_lock = threading.Lock()

        def target(control, progress):
            nonlocal counter
            for step in range(40):
                if not control.checkpoint():
                    return _result(RunStatus.STOPPED)
                with counter_lock:
                    counter += 1
                progress(
                    Progress(
                        fraction=(step + 1) / 40,
                        simulated_time_s=(step + 1) * 0.001,
                        step=step + 1,
                    )
                )
                time.sleep(0.003)
            return _result()

        controller = JobController[SimulationResult]().start(target)
        self.assertTrue(controller.wait_for_state(JobState.RUNNING, timeout=1.0))
        self.assertTrue(self._wait_until(lambda: counter >= 3))
        self.assertTrue(controller.pause(wait=True, timeout=1.0))
        self.assertEqual(controller.state, JobState.PAUSED)
        with counter_lock:
            paused_count = counter
        time.sleep(0.04)
        with counter_lock:
            self.assertEqual(counter, paused_count)
        controller.resume()
        self.assertTrue(controller.join(2.0))
        self.assertEqual(controller.state, JobState.COMPLETED)
        self.assertEqual(controller.result.status, RunStatus.COMPLETED)
        states = [transition.current for transition in controller.transitions]
        self.assertIn(JobState.PAUSING, states)
        self.assertIn(JobState.PAUSED, states)

    def test_soft_cancel_wakes_paused_job_and_never_marks_success(self) -> None:
        def target(control, progress):
            step = 0
            while step < 1000:
                if not control.checkpoint():
                    return _result(RunStatus.STOPPED)
                step += 1
                progress(
                    Progress(
                        fraction=min(step / 1000, 1.0),
                        simulated_time_s=step * 0.001,
                        step=step,
                    )
                )
                time.sleep(0.002)
            return _result()

        controller = JobController[SimulationResult]().start(target)
        self.assertTrue(controller.wait_for_state(JobState.RUNNING, timeout=1.0))
        self.assertTrue(controller.pause(timeout=1.0))
        controller.cancel("test cancellation")
        self.assertTrue(controller.join(1.0))
        self.assertEqual(controller.state, JobState.CANCELLED)
        self.assertEqual(controller.result.status, RunStatus.STOPPED)
        transitions = [(item.previous, item.current) for item in controller.transitions]
        self.assertIn((JobState.PAUSED, JobState.CANCELLING), transitions)
        self.assertIn((JobState.CANCELLING, JobState.CANCELLED), transitions)

    def test_illegal_transitions_are_rejected(self) -> None:
        controller = JobController[SimulationResult]()
        for operation in (controller.pause, controller.resume, controller.cancel):
            with self.assertRaises(InvalidStateTransition):
                operation()

        release = threading.Event()

        def target(control, progress):
            del control, progress
            release.wait(1.0)
            return _result()

        controller.start(target)
        with self.assertRaises(InvalidStateTransition):
            controller.start(target)
        release.set()
        self.assertTrue(controller.join(1.0))
        with self.assertRaises(InvalidStateTransition):
            controller.cancel()

    def test_pause_racing_with_final_step_can_complete(self) -> None:
        at_last_step = threading.Event()
        allow_return = threading.Event()

        def target(control, progress):
            del control, progress
            at_last_step.set()
            allow_return.wait(1.0)
            return "done"

        controller = JobController[str]().start(target)
        self.assertTrue(at_last_step.wait(1.0))
        self.assertTrue(controller.pause(wait=False))
        allow_return.set()
        self.assertTrue(controller.join(1.0))
        self.assertEqual(controller.state, JobState.COMPLETED)
        self.assertEqual(controller.result, "done")
        self.assertIn(
            (JobState.PAUSING, JobState.COMPLETED),
            [(item.previous, item.current) for item in controller.transitions],
        )

    def test_failure_writes_terminal_manifest_with_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "runtime.json"

            def target(control, progress):
                del control, progress
                raise RuntimeError("intentional failure")

            controller = JobController[object](manifest_path=manifest).start(target)
            self.assertTrue(controller.join(1.0))
            self.assertEqual(controller.state, JobState.FAILED)
            raw = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertTrue(raw["terminal"])
            self.assertEqual(raw["state"], "failed")
            self.assertEqual(raw["error"]["type"], "RuntimeError")
            self.assertIn("intentional failure", raw["error"]["traceback"])


class CLITests(unittest.TestCase):
    def test_runtime_source_hash_provider_detects_change_without_touching_tree(self) -> None:
        base_snapshot = source_tree_identity()
        self.assertTrue(base_snapshot["available"])
        snapshots = [
            {**base_snapshot, "value": "0" * 64},
            {**base_snapshot, "value": "1" * 64},
        ]

        def provider() -> dict[str, object]:
            return snapshots.pop(0)

        registry = BackendRegistry()
        registry.register("forward", _FakeBackend)
        config = replace(
            SimulationConfig(),
            drive=DriveConfig(
                initial_rpm=0.0,
                target_rpm=0.0,
                ramp_duration_s=0.0,
            ),
            run=RunConfig(duration_s=0.01, output_interval_s=0.01),
        )
        with tempfile.TemporaryDirectory() as directory:
            outcome = run_once(
                config,
                Path(directory) / "run",
                registry=registry,
                source_tree_provider=provider,
            )
            self.assertEqual(outcome.state, JobState.COMPLETED)
            self.assertFalse(outcome.succeeded)
            self.assertFalse(outcome.execution_source_identity_valid)
            self.assertEqual(snapshots, [])
            archived = load_run_archive(outcome.output)
            identity = archived.provenance["source_identity"]
            self.assertEqual(identity["source_tree_sha256_start"], "0" * 64)
            self.assertEqual(identity["source_tree_sha256_end"], "1" * 64)
            self.assertFalse(identity["unchanged"])
            self.assertFalse(identity["execution_source_identity_valid"])
            self.assertEqual(
                identity["reproducibility_status"],
                "invalid_source_tree_changed_between_boundaries",
            )
            self.assertFalse(archived.manifest["execution_source_identity_valid"])
            self.assertEqual(
                archived.manifest["provenance_reproducibility_status"],
                "invalid_source_tree_changed_between_boundaries",
            )

    def test_minimal_forward_run_creates_verified_archive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = SimulationConfig(
                backend=BackendMode.FORWARD,
                gas=ideal_test_reservoir(until_s=0.01),
                drive=DriveConfig(
                    initial_rpm=0.0,
                    target_rpm=0.0,
                    ramp_duration_s=0.0,
                ),
                run=RunConfig(duration_s=0.01, output_interval_s=0.005),
                forward=replace(
                    SimulationConfig().forward,
                    integration_time_step_s=0.001,
                    surface_radial_points=128,
                ),
            )
            config_path = root / "config.json"
            output = root / "run"
            save_config_json(config_path, config)
            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = cli.main(
                    [
                        "run",
                        "--backend",
                        "forward",
                        "--config",
                        str(config_path),
                        "--output",
                        str(output),
                        "--quiet",
                    ]
                )
            self.assertEqual(code, 0, stderr.getvalue())
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["state"], "completed")
            self.assertEqual(payload["completion_class"], "completed_full")
            self.assertEqual(payload["domain_status"], "IN_MODEL_DOMAIN")
            self.assertFalse(payload["out_of_model_domain"])
            self.assertIsNone(payload["first_out_of_domain_s"])
            self.assertEqual(
                payload["scientific_completion_class"], "completed_full"
            )
            self.assertEqual(payload["termination_reason"], "requested_duration_reached")
            self.assertFalse(payload["domain_limited"])
            self.assertTrue(payload["full_duration"])
            self.assertEqual(payload["requested_end_s"], config.run.duration_s)
            self.assertEqual(payload["actual_end_s"], config.run.duration_s)
            archived = load_run_archive(output)
            self.assertEqual(archived.result.status, RunStatus.COMPLETED)
            self.assertEqual(archived.result.backend, BackendMode.FORWARD)
            provenance = archived.provenance
            cpu = provenance["resources"]["process_cpu_time"]
            self.assertTrue(cpu["available"])
            self.assertEqual(cpu["unit"], "s")
            self.assertEqual(cpu["source"], "time.process_time")
            self.assertGreaterEqual(cpu["value"], 0.0)
            self.assertIn("JobController.start", cpu["start_sample_point"])
            self.assertIn("terminal state", cpu["end_sample_point"])
            self.assertIn("excludes elapsed sleep", cpu["clock_semantics"])
            peak = provenance["resources"]["peak_resident_set_size"]
            self.assertEqual(peak["unit"], "byte")
            self.assertEqual(
                peak["sample_point"],
                "after backend termination and before result archive serialization",
            )
            if platform.system() == "Linux":
                self.assertTrue(peak["available"])
                self.assertEqual(peak["native_unit"], "KiB")
                self.assertEqual(peak["conversion_to_bytes"], 1024)
            self.assertIn("not a per-run increment", peak["attribution_note"])
            source_identity = provenance["source_identity"]
            disk_tree = source_identity["disk_content_tree"]
            self.assertTrue(disk_tree["execution_start"]["available"])
            self.assertTrue(disk_tree["execution_end"]["available"])
            self.assertEqual(
                disk_tree["execution_start"]["algorithm"], "sha256"
            )
            self.assertTrue(disk_tree["boundary_match"])
            self.assertEqual(
                source_identity["source_tree_sha256_start"],
                source_identity["source_tree_sha256_end"],
            )
            self.assertTrue(source_identity["unchanged"])
            self.assertTrue(source_identity["execution_source_identity_valid"])
            self.assertEqual(
                source_identity["reproducibility_status"],
                "qualified_source_tree_boundary_match",
            )
            self.assertFalse(source_identity["loaded_module_bytes_verified"])
            invocation = provenance["invocation"]
            self.assertEqual(
                invocation["entry_point"], "airvortex.runtime.run_once"
            )
            self.assertFalse(invocation["raw_argv_stored"])
            self.assertFalse(invocation["absolute_paths_stored"])
            invocation_json = json.dumps(invocation, ensure_ascii=False)
            self.assertNotIn(str(root), invocation_json)
            self.assertNotIn(str(config_path), invocation_json)
            self.assertNotIn(str(output), invocation_json)

    def test_single_run_and_sweep_report_post_contact_scientific_class(self) -> None:
        time_s = np.array([0.0, 1.0, 2.0, 3.0])

        class PostContactForwardBackend:
            mode = BackendMode.FORWARD

            def run(self, config, control=None, progress=None):
                del config, control, progress
                return SimulationResult(
                    backend=BackendMode.FORWARD,
                    time_s=time_s,
                    observables={"funnel_depth_m": np.arange(4.0)},
                    events=(Event(2.0, "CONNECTED_PROXY"),),
                    metadata={
                        "domain_status": "OUT_OF_MODEL_DOMAIN",
                        "observable_units": {"funnel_depth_m": "m"},
                        "diagnostics": {
                            "termination_reason": "configured_duration_reached"
                        },
                    },
                )

        registry = BackendRegistry()
        registry.register("forward", PostContactForwardBackend)
        config = replace(
            SimulationConfig(),
            backend=BackendMode.FORWARD,
            drive=DriveConfig(
                initial_rpm=1500.0,
                target_rpm=1500.0,
                ramp_duration_s=0.0,
            ),
            run=RunConfig(duration_s=3.0, output_interval_s=1.0),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.json"
            save_config_json(config_path, config)
            single = run_once(config, root / "single", registry=registry)
            self.assertTrue(single.succeeded)
            self.assertEqual(single.completion_class, "completed_full")
            self.assertEqual(single.domain_status, "OUT_OF_MODEL_DOMAIN")
            self.assertTrue(single.out_of_model_domain)
            self.assertEqual(single.first_out_of_domain_s, 2.0)
            self.assertEqual(
                single.scientific_completion_class,
                "completed_with_out_of_model_domain",
            )

            stdout = io.StringIO()
            stderr = io.StringIO()
            with patch.object(cli, "run_once", return_value=single):
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    code = cli.main(
                        [
                            "run",
                            "--backend",
                            "forward",
                            "--config",
                            str(config_path),
                            "--output",
                            str(root / "cli_single"),
                            "--quiet",
                        ]
                    )
            self.assertEqual(code, 0, stderr.getvalue())
            run_payload = json.loads(stdout.getvalue())
            self.assertEqual(run_payload["completion_class"], "completed_full")
            self.assertEqual(
                run_payload["scientific_completion_class"],
                "completed_with_out_of_model_domain",
            )
            self.assertEqual(run_payload["domain_status"], "OUT_OF_MODEL_DOMAIN")
            self.assertTrue(run_payload["out_of_model_domain"])
            self.assertEqual(run_payload["first_out_of_domain_s"], 2.0)

            sweep_root = root / "sweep"
            first = run_sweep(
                config,
                "drive.target_rpm",
                [1500.0],
                sweep_root,
                registry=registry,
            )
            self.assertTrue(first.succeeded)
            self.assertEqual(first.completed_full, 1)
            self.assertEqual(first.completed_with_out_of_model_domain, 1)
            self.assertTrue(first.out_of_model_domain)
            manifest = json.loads(
                (sweep_root / "sweep_manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                manifest["status"], "completed_with_out_of_model_domain"
            )
            self.assertEqual(manifest["completed_full"], 1)
            self.assertEqual(manifest["completed_with_out_of_model_domain"], 1)
            case = manifest["cases"][0]
            self.assertEqual(case["completion_class"], "completed_full")
            self.assertEqual(
                case["scientific_completion_class"],
                "completed_with_out_of_model_domain",
            )
            self.assertEqual(case["domain_status"], "OUT_OF_MODEL_DOMAIN")
            self.assertEqual(case["first_out_of_domain_s"], 2.0)

            resumed = run_sweep(
                config,
                "drive.target_rpm",
                [1500.0],
                sweep_root,
                registry=registry,
            )
            self.assertTrue(resumed.succeeded)
            self.assertEqual(resumed.skipped_full, 1)
            self.assertEqual(resumed.skipped_with_out_of_model_domain, 1)
            self.assertTrue(resumed.out_of_model_domain)
            resumed_manifest = json.loads(
                (sweep_root / "sweep_manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                resumed_manifest["status"],
                "completed_with_out_of_model_domain",
            )
            self.assertEqual(
                resumed_manifest["cases"][0]["scientific_completion_class"],
                "completed_with_out_of_model_domain",
            )

            stdout = io.StringIO()
            stderr = io.StringIO()
            with patch.object(cli, "run_sweep", return_value=resumed):
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    code = cli.main(
                        [
                            "sweep",
                            "--config",
                            str(config_path),
                            "--parameter",
                            "drive.target_rpm",
                            "--start",
                            "1500",
                            "--stop",
                            "1500",
                            "--num",
                            "1",
                            "--output",
                            str(root / "cli_sweep"),
                            "--quiet",
                        ]
                    )
            self.assertEqual(code, 0, stderr.getvalue())
            sweep_payload = json.loads(stdout.getvalue())
            self.assertEqual(
                sweep_payload["status"],
                "completed_with_out_of_model_domain",
            )
            self.assertEqual(sweep_payload["skipped_full"], 1)
            self.assertEqual(
                sweep_payload["skipped_with_out_of_model_domain"], 1
            )
            self.assertTrue(sweep_payload["out_of_model_domain"])

    def test_validate_config_backend_override(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            save_config_json(path, SimulationConfig())
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = cli.main(["validate-config", str(path), "--backend", "forward"])
            self.assertEqual(code, 0)
            self.assertTrue(json.loads(stdout.getvalue())["valid"])

    def test_linear_and_log_sweep_values_include_endpoints(self) -> None:
        self.assertEqual(linear_values(1.0, 3.0, 3), [1.0, 2.0, 3.0])
        values = logarithmic_values(1.0, 100.0, 3)
        np.testing.assert_allclose(values, [1.0, 10.0, 100.0], rtol=1e-14)
        with self.assertRaises(ValueError):
            logarithmic_values(0.0, 10.0, 2)

    def test_sweep_resume_skips_verified_completed_cases(self) -> None:
        calls = 0

        class CountingBackend:
            mode = BackendMode.FORWARD

            def run(self, config, control=None, progress=None):
                nonlocal calls
                del config, control, progress
                calls += 1
                return _result()

        registry = BackendRegistry()
        registry.register("forward", CountingBackend)
        base = replace(
            SimulationConfig(),
            drive=DriveConfig(
                initial_rpm=0.0,
                target_rpm=100.0,
                ramp_duration_s=0.0,
            ),
            run=RunConfig(duration_s=0.01, output_interval_s=0.01),
        )
        with tempfile.TemporaryDirectory() as directory:
            first = run_sweep(
                base,
                "drive.target_rpm",
                [100.0, 200.0],
                Path(directory) / "sweep",
                registry=registry,
            )
            self.assertTrue(first.succeeded)
            self.assertEqual(calls, 2)
            resumed = run_sweep(
                base,
                "drive.target_rpm",
                [100.0, 200.0],
                Path(directory) / "sweep",
                registry=registry,
            )
            self.assertTrue(resumed.succeeded)
            self.assertEqual(resumed.skipped, 2)
            self.assertEqual(resumed.completed, 0)
            self.assertEqual(calls, 2, "resume must not repeat completed jobs")

    def test_domain_limit_is_not_collapsed_into_full_duration_in_run_or_sweep(self) -> None:
        class DomainLimitedBackend:
            mode = BackendMode.FORWARD

            def run(self, config, control=None, progress=None):
                del config, control, progress
                return SimulationResult(
                    backend=BackendMode.FORWARD,
                    time_s=np.array([0.0, 0.004]),
                    observables={"surface_depth_m": np.array([0.0, 0.001])},
                    metadata={
                        "diagnostics": {
                            "termination_reason": (
                                "resolved_instantaneous_connection_candidate_domain_limit"
                            )
                        }
                    },
                )

        registry = BackendRegistry()
        registry.register("forward", DomainLimitedBackend)
        config = replace(
            SimulationConfig(),
            drive=DriveConfig(
                initial_rpm=0.0,
                target_rpm=100.0,
                ramp_duration_s=0.0,
            ),
            run=RunConfig(duration_s=0.01, output_interval_s=0.002),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            single = run_once(config, root / "single", registry=registry)
            self.assertTrue(single.succeeded)
            self.assertTrue(single.domain_limited)
            self.assertFalse(single.full_duration)
            self.assertEqual(single.requested_end_s, 0.01)
            self.assertEqual(single.actual_end_s, 0.004)
            self.assertEqual(single.completion_class, "completed_domain_limited")

            first = run_sweep(
                config,
                "drive.target_rpm",
                [100.0],
                root / "sweep",
                registry=registry,
            )
            self.assertTrue(first.succeeded)
            self.assertEqual(first.completed_full, 0)
            self.assertEqual(first.completed_domain_limited, 1)
            manifest = json.loads(
                (root / "sweep" / "sweep_manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["status"], "completed_with_domain_limit")
            self.assertTrue(manifest["domain_limited"])
            self.assertEqual(
                manifest["cases"][0]["status"], "completed_domain_limited"
            )

            resumed = run_sweep(
                config,
                "drive.target_rpm",
                [100.0],
                root / "sweep",
                registry=registry,
            )
            self.assertEqual(resumed.skipped_full, 0)
            self.assertEqual(resumed.skipped_domain_limited, 1)

    def test_failed_run_has_terminal_manifest_and_is_not_success(self) -> None:
        class FailingBackend:
            mode = BackendMode.FORWARD

            def run(self, config, control=None, progress=None):
                del config, control, progress
                raise ArithmeticError("solver exploded")

        registry = BackendRegistry()
        registry.register("forward", FailingBackend)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "failed"
            outcome = run_once(SimulationConfig(), output, registry=registry)
            self.assertEqual(outcome.state, JobState.FAILED)
            raw = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(raw["state"], "failed")
            self.assertTrue(raw["terminal"])
            self.assertEqual(raw["archive_format"], "airvortex-runtime-terminal-v2")
            self.assertFalse(raw["complete_run_archive"])
            self.assertEqual(
                raw["artifact_class"],
                "terminal_diagnostic_without_simulation_result",
            )
            self.assertIn("solver exploded", (output / "traceback.txt").read_text())
            provenance = json.loads(
                (output / "provenance.json").read_text(encoding="utf-8")
            )
            self.assertTrue(
                provenance["resources"]["process_cpu_time"]["available"]
            )
            self.assertEqual(
                provenance["resources"]["peak_resident_set_size"]["unit"],
                "byte",
            )
            self.assertIn("source_tree_sha256_start", provenance["source_identity"])
            for name, descriptor in raw["files"].items():
                payload = (output / name).read_bytes()
                self.assertEqual(len(payload), descriptor["size_bytes"])
                self.assertEqual(
                    hashlib.sha256(payload).hexdigest(), descriptor["sha256"]
                )
            self.assertFalse(raw["integrity_model"]["manifest_self_authenticated"])

    def test_1500rpm_three_second_pair_excludes_forward_post_contact_tail(self) -> None:
        time_s = np.array([0.0, 1.0, 2.0, 3.0])

        class PostContactForwardBackend:
            mode = BackendMode.FORWARD

            def run(self, config, control=None, progress=None):
                del config, control, progress
                return synthetic_signal_result(
                    backend=BackendMode.FORWARD,
                    first_invalid_time_s=2.0,
                    time_s=time_s,
                    # The deliberately divergent t=3 proxy value must never
                    # enter the model-to-model RMSE after contact at t=2.
                    observables={
                        "funnel_depth_m": np.array([0.0, 0.0, 0.0, 100.0])
                    },
                    events=(
                        Event(
                            2.0,
                            "CONNECTED_PROXY",
                            "constructed 1500 rpm post-contact counterexample",
                        ),
                    ),
                    metadata={
                        "domain_status": "OUT_OF_MODEL_DOMAIN",
                        "observable_units": {"funnel_depth_m": "m"},
                        "diagnostics": {
                            "termination_reason": "configured_duration_reached"
                        },
                    },
                )

        class InDomainHighFidelityBackend:
            mode = BackendMode.HIGH_FIDELITY

            def run(self, config, control=None, progress=None):
                del config, control, progress
                return synthetic_signal_result(
                    backend=BackendMode.HIGH_FIDELITY,
                    time_s=time_s,
                    observables={"funnel_depth_m": np.zeros(4)},
                    metadata={
                        "domain_status": "IN_MODEL_DOMAIN",
                        "observable_units": {"funnel_depth_m": "m"},
                        "diagnostics": {
                            "termination_reason": "configured_duration_reached"
                        },
                    },
                )

        registry = BackendRegistry()
        registry.register("forward", PostContactForwardBackend)
        registry.register("high_fidelity", InDomainHighFidelityBackend)
        config = replace(
            SimulationConfig(),
            drive=DriveConfig(
                initial_rpm=1500.0,
                target_rpm=1500.0,
                ramp_duration_s=0.0,
            ),
            run=RunConfig(duration_s=3.0, output_interval_s=1.0),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "paired_1500rpm_3s"
            comparison_path = run_paired_compare(config, root, registry=registry)
            comparison = json.loads(
                (comparison_path / "comparison.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                comparison["status"], "completed_with_out_of_model_domain"
            )
            self.assertEqual(comparison["valid_time_end"], 1.0)
            self.assertTrue(comparison["truncated_to_common_valid_domain"])
            self.assertEqual(
                comparison["domains"]["left"]["domain_status"],
                "OUT_OF_MODEL_DOMAIN",
            )
            self.assertEqual(
                comparison["domains"]["left"]["first_out_of_domain_s"], 2.0
            )
            self.assertEqual(
                comparison["metrics"]["funnel_depth_m"]["common_interval_s"],
                [0.0, 1.0],
            )
            self.assertEqual(
                comparison["metrics"]["funnel_depth_m"]["rmse"], 0.0
            )
            comparison_manifest = json.loads(
                (comparison_path / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                comparison_manifest["status"],
                "completed_with_out_of_model_domain",
            )
            self.assertEqual(comparison_manifest["valid_time_end"], 1.0)
            self.assertTrue(
                comparison_manifest["truncated_to_common_valid_domain"]
            )

            paired = json.loads(
                (root / "paired_manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                paired["status"], "completed_with_out_of_model_domain"
            )
            self.assertFalse(paired["domain_limited"])
            self.assertTrue(paired["out_of_model_domain"])
            self.assertEqual(paired["valid_time_end"], 1.0)
            self.assertTrue(paired["truncated_to_common_valid_domain"])
            self.assertEqual(
                paired["backend_terminations"]["forward"]["completion_class"],
                "completed_with_out_of_model_domain",
            )
            self.assertEqual(
                paired["backend_terminations"]["forward"][
                    "first_out_of_domain_s"
                ],
                2.0,
            )

            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = cli.main(
                    [
                        "compare",
                        str(root / "forward"),
                        str(root / "high_fidelity"),
                        "--output",
                        str(root / "cli_comparison"),
                    ]
                )
            self.assertEqual(code, 0, stderr.getvalue())
            cli_payload = json.loads(stdout.getvalue())
            self.assertEqual(
                cli_payload["status"],
                "completed_with_out_of_model_domain",
            )
            self.assertEqual(cli_payload["valid_time_end"], 1.0)
            self.assertTrue(
                cli_payload["truncated_to_common_valid_domain"]
            )

    def test_compare_rejects_metrics_when_valid_domain_has_no_duration(self) -> None:
        time_s = np.array([0.0, 1.0, 2.0, 3.0])
        default = SimulationConfig()
        config = replace(
            default,
            drive=DriveConfig(
                initial_rpm=1500.0,
                target_rpm=1500.0,
                ramp_duration_s=0.0,
            ),
            run=RunConfig(duration_s=3.0, output_interval_s=1.0),
        )
        forward_result = SimulationResult(
            backend=BackendMode.FORWARD,
            time_s=time_s,
            observables={"funnel_depth_m": np.arange(4.0)},
            events=(Event(0.0, "CONNECTED_PROXY"),),
            metadata={
                "domain_status": "OUT_OF_MODEL_DOMAIN",
                "observable_units": {"funnel_depth_m": "m"},
            },
        )
        high_fidelity_result = SimulationResult(
            backend=BackendMode.HIGH_FIDELITY,
            time_s=time_s,
            observables={"funnel_depth_m": np.zeros(4)},
            metadata={"observable_units": {"funnel_depth_m": "m"}},
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            save_run_archive(
                root / "forward",
                replace(config, backend=BackendMode.FORWARD),
                forward_result,
            )
            save_run_archive(
                root / "high_fidelity",
                replace(config, backend=BackendMode.HIGH_FIDELITY),
                high_fidelity_result,
            )
            comparison_path = compare_archives(
                root / "forward",
                root / "high_fidelity",
                root / "comparison",
            )
            comparison = json.loads(
                (comparison_path / "comparison.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                comparison["status"],
                "rejected_insufficient_common_valid_domain",
            )
            self.assertEqual(
                comparison["completion_class"],
                "completed_with_out_of_model_domain",
            )
            self.assertIsNone(comparison["valid_time_end"])
            self.assertFalse(comparison["common_valid_domain_sufficient"])
            self.assertEqual(comparison["metrics"], {})
            self.assertIn(
                "insufficient_common_valid_domain",
                comparison["rejected"]["funnel_depth_m"],
            )

    def test_hf_domain_limit_is_not_labelled_as_post_domain_output(self) -> None:
        config = replace(
            SimulationConfig(),
            run=RunConfig(duration_s=3.0, output_interval_s=1.0),
        )
        forward_result = synthetic_signal_result(
            backend=BackendMode.FORWARD,
            time_s=np.array([0.0, 1.0, 2.0, 3.0]),
            observables={"funnel_depth_m": np.zeros(4)},
            metadata={
                "domain_status": "IN_MODEL_DOMAIN",
                "observable_units": {"funnel_depth_m": "m"},
            },
        )
        high_fidelity_result = synthetic_signal_result(
            backend=BackendMode.HIGH_FIDELITY,
            time_s=np.array([0.0, 1.0, 2.0]),
            observables={"funnel_depth_m": np.zeros(3)},
            events=(Event(2.0, "air_connection_candidate"),),
            metadata={
                "observable_units": {"funnel_depth_m": "m"},
                "diagnostics": {
                    "termination_reason": (
                        "resolved_instantaneous_connection_candidate_domain_limit"
                    )
                },
            },
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            save_run_archive(
                root / "forward",
                replace(config, backend=BackendMode.FORWARD),
                forward_result,
            )
            save_run_archive(
                root / "high_fidelity",
                replace(config, backend=BackendMode.HIGH_FIDELITY),
                high_fidelity_result,
            )
            comparison_path = compare_archives(
                root / "forward",
                root / "high_fidelity",
                root / "comparison",
            )
            comparison = json.loads(
                (comparison_path / "comparison.json").read_text(encoding="utf-8")
            )
            right_domain = comparison["domains"]["right"]
            self.assertEqual(
                comparison["status"], "completed_with_domain_limit"
            )
            self.assertEqual(comparison["valid_time_end"], 2.0)
            self.assertFalse(comparison["truncated_to_common_valid_domain"])
            self.assertEqual(
                right_domain["domain_status"], "MODEL_DOMAIN_LIMIT_REACHED"
            )
            self.assertTrue(right_domain["model_domain_limit"])
            self.assertFalse(right_domain["out_of_model_domain"])
            self.assertIsNone(right_domain["first_out_of_domain_s"])

    def test_paired_compare_runs_both_backends_on_common_observables(self) -> None:
        default = SimulationConfig()
        config = replace(
            default,
            gas=ideal_test_reservoir(until_s=0.0002),
            drive=DriveConfig(
                initial_rpm=0.0,
                target_rpm=0.0,
                ramp_duration_s=0.0,
            ),
            run=RunConfig(duration_s=0.0002, output_interval_s=0.0001),
            forward=replace(
                default.forward,
                integration_time_step_s=0.0001,
                surface_radial_points=128,
            ),
            high_fidelity=replace(
                default.high_fidelity,
                radial_cells=16,
                azimuthal_cells=16,
                axial_cells=24,
                time_step_s=0.0001,
                pressure_relative_tolerance=1.0e-6,
                pressure_max_iterations=800,
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "paired"
            comparison_path = run_paired_compare(config, root)
            payload = json.loads(
                (comparison_path / "comparison.json").read_text(encoding="utf-8")
            )
            self.assertEqual(payload["comparison_format_version"], 2)
            self.assertEqual(payload["status"], "rejected_insufficient_common_valid_domain")
            self.assertIsNone(payload["valid_time_end"])
            self.assertFalse(payload["truncated_to_common_valid_domain"])
            # Both real backends run and archive; HF3D has no declared
            # quantitative qualification, so no cross-model metrics are made.
            self.assertEqual(payload["metrics"], {})
            self.assertEqual(payload["domains"]["right"]["quantitative_validity_status"], "legacy_unassessed")
            self.assertGreater(load_run_archive(root / "forward").result.time_s.size, 1)
            self.assertGreater(load_run_archive(root / "high_fidelity").result.time_s.size, 1)
            paired = json.loads(
                (root / "paired_manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(paired["status"], "rejected_insufficient_common_valid_domain")
            self.assertEqual(paired["paired_run_format_version"], 2)
            self.assertFalse(paired["domain_limited"])
            self.assertFalse(paired["out_of_model_domain"])
            self.assertIsNone(paired["valid_time_end"])
            self.assertFalse(paired["truncated_to_common_valid_domain"])
            self.assertEqual(paired["requested_end_s"], config.run.duration_s)
            self.assertEqual(
                set(paired["backend_terminations"]),
                {"forward", "high_fidelity"},
            )
            for termination in paired["backend_terminations"].values():
                self.assertIn("termination_reason", termination)
                self.assertIn("actual_end_s", termination)
                self.assertIn("domain_status", termination)
                self.assertIn("first_out_of_domain_s", termination)
                self.assertFalse(termination["domain_limited"])


if __name__ == "__main__":
    unittest.main()
