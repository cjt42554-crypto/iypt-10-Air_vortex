from __future__ import annotations

import copy
import cProfile
import os
import sqlite3
import sys
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import analyse_p0p2v2_campaign as analysis_module
import p0p2v2_transaction as transaction_module
import run_p0p2v2_campaign as campaign_module
from p0p2v2_allocation import (
    audit_known_probe,
    audit_observer_payload,
    build_native_artifacts,
    device_allocation_crosscheck,
    open_observer_fd,
    run_probe,
)
from p0p2v2_contract import (
    EXPANDED_EXECUTION_SCHEDULE,
    P0PerformanceError,
    effective_config,
    sample_identity,
    strict_json,
    validate_effective_payload,
)
from p0p2v2_instrumentation import (
    IntervalRegistry,
    RangeRecord,
    TransparentCallable,
    audit_host_semantic_evidence,
    coverage_report,
    parse_pstats,
    range_records_from_manifest,
)
from p0p2v2_nsys import REQUIRED_STATS, audit_nsys_evidence, profile_command
from p0p2v2_numerical import (
    archive_numerical_payload,
    canonical_arrays_identity,
    compare_payloads,
)
from p0p2v2_telemetry import (
    ProcessSample,
    audit_background_records,
    audit_ticket_machine_identity,
    evaluate_instantaneous_environment,
    full_environment_gate,
    telemetry_coverage,
    verify_pid_lifetime,
)
from p0p2v2_transaction import DirectoryPublicationTransaction, SampleTransaction
from run_p0p2v2_campaign import parse_args, sample_launch_spec


def _range(
    call_id: int,
    name: str,
    *,
    parent: int | None,
    start: int,
    end: int,
    child_ns: int = 0,
    attributed: bool = True,
) -> RangeRecord:
    return RangeRecord(
        call_id=call_id,
        name=name,
        parent_call_id=parent,
        thread_id=1,
        start_ns=start,
        end_ns=end,
        inclusive_ns=end - start,
        child_ns=child_ns,
        exclusive_ns=end - start - child_ns,
        attributed=attributed,
        status="returned",
        binding=None,
    )


class InstrumentationTests(unittest.TestCase):
    def test_transparent_wrapper_preserves_arguments_result_and_exception(self) -> None:
        ticks = iter((10, 20, 30, 40))
        registry = IntervalRegistry(clock_ns=lambda: next(ticks))
        marker = object()
        calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

        def original(*args: Any, **kwargs: Any) -> object:
            calls.append((args, kwargs))
            return marker

        wrapped = TransparentCallable(original, name="synthetic", registry=registry)
        argument = object()
        self.assertIs(wrapped(argument, keyword=argument), marker)
        self.assertEqual(calls, [((argument,), {"keyword": argument})])
        self.assertEqual(wrapped.call_count, 1)
        self.assertEqual(registry.records()[0].status, "returned")

        class OriginalFailure(RuntimeError):
            pass

        def failing() -> None:
            raise OriginalFailure("unchanged-message")

        failed = TransparentCallable(failing, name="failure", registry=registry)
        with self.assertRaisesRegex(OriginalFailure, "unchanged-message"):
            failed()
        self.assertEqual(registry.records()[-1].status, "raised")
        self.assertEqual(failed.call_count, 1)

    def test_nested_exclusive_coverage_thresholds(self) -> None:
        passing = (
            _range(1, "process", parent=None, start=0, end=100_000, child_ns=100_000),
            _range(
                2,
                "solver_phase",
                parent=1,
                start=0,
                end=100_000,
                child_ns=100_000,
            ),
            _range(3, "named", parent=2, start=0, end=92_000),
            _range(
                4,
                "unknown-a",
                parent=2,
                start=92_000,
                end=96_000,
                attributed=False,
            ),
            _range(
                5,
                "unknown-b",
                parent=2,
                start=96_000,
                end=100_000,
                attributed=False,
            ),
        )
        self.assertTrue(
            coverage_report(
                passing, child_wall_ns=100_000, solver_range_name="solver_phase"
            )["gate_pass"]
        )

        below_ninety = (
            _range(1, "process", parent=None, start=0, end=100_000, child_ns=100_000),
            _range(
                2,
                "solver_phase",
                parent=1,
                start=0,
                end=100_000,
                child_ns=98_000,
            ),
            _range(3, "named", parent=2, start=0, end=89_999),
            _range(
                4,
                "unknown-a",
                parent=2,
                start=89_999,
                end=93_999,
                attributed=False,
            ),
            _range(
                5,
                "unknown-b",
                parent=2,
                start=93_999,
                end=98_000,
                attributed=False,
            ),
        )
        report = coverage_report(
            below_ninety,
            child_wall_ns=100_000,
            solver_range_name="solver_phase",
        )
        self.assertFalse(report["gate_pass"])
        self.assertFalse(report["gates"]["named_solver_exclusive_at_least_90_percent"])

        single_unknown_five_percent = (
            _range(1, "process", parent=None, start=0, end=100_000, child_ns=100_000),
            _range(
                2,
                "solver_phase",
                parent=1,
                start=0,
                end=100_000,
                child_ns=100_000,
            ),
            _range(3, "named", parent=2, start=0, end=95_000),
            _range(
                4,
                "unknown",
                parent=2,
                start=95_000,
                end=100_000,
                attributed=False,
            ),
        )
        report = coverage_report(
            single_unknown_five_percent,
            child_wall_ns=100_000,
            solver_range_name="solver_phase",
        )
        self.assertFalse(report["gate_pass"])
        self.assertFalse(report["gates"]["no_single_unknown_region_at_least_5_percent"])

    def test_serialized_host_ranges_are_strictly_reparsed_and_recomputed(self) -> None:
        records = (
            _range(
                1,
                "P0P2V2_PROCESS_START",
                parent=None,
                start=0,
                end=100_000,
                child_ns=100_000,
            ),
            _range(
                2,
                "solver_phase",
                parent=1,
                start=0,
                end=100_000,
                child_ns=100_000,
            ),
            _range(3, "named", parent=2, start=0, end=92_000),
            _range(
                4,
                "unknown-a",
                parent=2,
                start=92_000,
                end=96_000,
                attributed=False,
            ),
            _range(
                5,
                "unknown-b",
                parent=2,
                start=96_000,
                end=100_000,
                attributed=False,
            ),
        )
        manifest = [asdict(record) for record in records]
        self.assertEqual(range_records_from_manifest(manifest), records)
        self.assertTrue(
            audit_host_semantic_evidence(manifest, child_wall_ns=100_000)[
                "gate_pass"
            ]
        )

        inconsistent = [dict(record) for record in manifest]
        inconsistent[2]["inclusive_ns"] += 1
        with self.assertRaises(P0PerformanceError):
            range_records_from_manifest(inconsistent)

        duplicate = [dict(record) for record in manifest]
        duplicate[-1]["call_id"] = 4
        with self.assertRaises(P0PerformanceError):
            range_records_from_manifest(duplicate)

        raised = [dict(record) for record in manifest]
        raised[-1]["status"] = "raised"
        self.assertFalse(
            audit_host_semantic_evidence(raised, child_wall_ns=100_000)[
                "gate_pass"
            ]
        )

    def test_cprofile_parser_reads_real_call_graph(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "profile.pstats"
            profile = cProfile.Profile()
            profile.runcall(sum, range(100))
            profile.dump_stats(path)
            report = parse_pstats(path)
            self.assertGreater(report["total_calls"], 0)
            self.assertGreater(report["row_count_total"], 0)
            self.assertTrue(report["source_sha256"])


class ContractAndLaunchTests(unittest.TestCase):
    def test_three_benchmarks_change_only_duration(self) -> None:
        for benchmark in ("B1", "B2", "B3"):
            with self.subTest(benchmark=benchmark):
                _config, identity = effective_config(benchmark)
                self.assertEqual(set(identity["differences"]), {"run.duration_s"})

    def test_v1_0002_duration_and_hash_drift_are_exact_negative_cases(self) -> None:
        payload, _identity = effective_config("B1")
        invalid_duration = copy.deepcopy(payload)
        invalid_duration["run"]["duration_s"] = 0.002
        with self.assertRaises(P0PerformanceError):
            validate_effective_payload("B1", invalid_duration)
        invalid_hash = copy.deepcopy(payload)
        invalid_hash["run"]["output_interval_s"] = 0.0005
        with self.assertRaises(P0PerformanceError):
            validate_effective_payload("B1", invalid_hash)

    def test_frozen_schedule_and_modes_have_no_selective_cli(self) -> None:
        self.assertEqual(len(EXPANDED_EXECUTION_SCHEDULE), 18)
        self.assertEqual(len(set(EXPANDED_EXECUTION_SCHEDULE)), 18)
        for sample_id in EXPANDED_EXECUTION_SCHEDULE[:12]:
            self.assertEqual(sample_identity(sample_id)["mode"], "unprofiled")
        with self.assertRaises(SystemExit):
            parse_args(["--execute", "--resume"])
        with self.assertRaises(SystemExit):
            parse_args(["--execute", "--only", "B1"])

    def test_launch_modes_are_strictly_separated(self) -> None:
        build_native_artifacts()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = {
                "OPENBLAS_NUM_THREADS": "99",
                "OMP_NUM_THREADS": "99",
                "OMP_DYNAMIC": "TRUE",
            }
            unprofiled = sample_launch_spec(
                root / "u",
                "P0P2V2_WARMUP_B1",
                source,
                sequence_index=0,
                launch_token="token",
                orchestrator_pid=os.getpid(),
            )
            self.assertNotIn("P0P2V2_CPROFILE_ACTIVE", unprofiled["environment"])
            self.assertNotIn("P0P2V2_NSYS_ACTIVE", unprofiled["environment"])
            self.assertNotIn("LD_PRELOAD", unprofiled["environment"])
            self.assertEqual(unprofiled["environment"]["OMP_DYNAMIC"], "FALSE")
            self.assertNotIn("OPENBLAS_NUM_THREADS", unprofiled["environment"])

            host = sample_launch_spec(
                root / "h",
                "P0P2V2_PROFILED_B2_HOST",
                source,
                sequence_index=12,
                launch_token="token",
                orchestrator_pid=os.getpid(),
            )
            self.assertEqual(host["environment"]["P0P2V2_CPROFILE_ACTIVE"], "1")
            self.assertNotIn("P0P2V2_NSYS_ACTIVE", host["environment"])

            cuda = sample_launch_spec(
                root / "c",
                "P0P2V2_PROFILED_B2_CUDA",
                source,
                sequence_index=13,
                launch_token="token",
                orchestrator_pid=os.getpid(),
            )
            self.assertEqual(cuda["environment"]["P0P2V2_NSYS_ACTIVE"], "1")
            self.assertIn("--trace=cuda,nvtx", cuda["command"])
            self.assertIn("--sample=none", cuda["command"])
            self.assertIn("--cpuctxsw=none", cuda["command"])
            self.assertFalse(
                any("cuda-memory-usage" in item for item in cuda["command"])
            )

            descriptor = open_observer_fd()
            try:
                memory = sample_launch_spec(
                    root / "m",
                    "P0P2V2_PROFILED_B2_MEMORY",
                    source,
                    sequence_index=14,
                    launch_token="token",
                    orchestrator_pid=os.getpid(),
                    observer_fd=descriptor,
                )
            finally:
                os.close(descriptor)
            self.assertEqual(
                memory["environment"]["P0P2V2_ALLOC_OBSERVER_ACTIVE"], "1"
            )
            self.assertNotIn("P0P2V2_CPROFILE_ACTIVE", memory["environment"])
            self.assertNotIn("P0P2V2_NSYS_ACTIVE", memory["environment"])

    def test_nsys_launcher_contains_only_qualified_options(self) -> None:
        command = profile_command(
            output_base=Path("/tmp/synthetic"), target_command=["python", "probe.py"]
        )
        self.assertIn("--trace=cuda,nvtx", command)
        self.assertIn("--sample=none", command)
        self.assertIn("--cpuctxsw=none", command)
        for prohibited in ("osrt", "cuda-memory-usage", "gpu-metrics"):
            self.assertFalse(any(prohibited in item for item in command))


def _write_csv(path: Path, header: str, rows: list[str]) -> None:
    path.write_text("\n".join([header, *rows, ""]), encoding="utf-8")


def _nsys_fixture(root: Path) -> dict[str, Any]:
    target_pid = 123
    global_pid = target_pid << 24
    global_tid = global_pid | 7
    report = root / "trace.nsys-rep"
    report.write_bytes(b"synthetic-report")
    database = root / "trace.sqlite"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE CUPTI_ACTIVITY_KIND_KERNEL
          (start INTEGER, end INTEGER, deviceId INTEGER, contextId INTEGER,
           globalPid INTEGER, demangledName INTEGER);
        CREATE TABLE CUPTI_ACTIVITY_KIND_MEMCPY
          (start INTEGER, end INTEGER, deviceId INTEGER, contextId INTEGER,
           globalPid INTEGER, bytes INTEGER, copyKind INTEGER);
        CREATE TABLE CUPTI_ACTIVITY_KIND_RUNTIME
          (start INTEGER, end INTEGER, globalTid INTEGER, correlationId INTEGER,
           nameId INTEGER, returnValue INTEGER);
        CREATE TABLE ENUM_CUDA_MEMCPY_OPER (id INTEGER, label TEXT);
        CREATE TABLE NVTX_EVENTS
          (start INTEGER, end INTEGER, text TEXT, textId INTEGER, globalTid INTEGER);
        CREATE TABLE PROCESSES (pid INTEGER, globalPid INTEGER);
        CREATE TABLE StringIds (id INTEGER, value TEXT);
        CREATE TABLE TARGET_INFO_CUDA_CONTEXT_INFO
          (contextId INTEGER, deviceId INTEGER, processId INTEGER);
        CREATE TABLE TARGET_INFO_CUDA_DEVICE (deviceId INTEGER);
        """
    )
    connection.executemany(
        "INSERT INTO StringIds VALUES (?, ?)",
        [(1, "synthetic_kernel"), (2, "cudaMalloc")],
    )
    connection.execute("INSERT INTO PROCESSES VALUES (?, ?)", (target_pid, global_pid))
    connection.execute(
        "INSERT INTO TARGET_INFO_CUDA_CONTEXT_INFO VALUES (1, 0, ?)",
        (target_pid,),
    )
    connection.execute("INSERT INTO TARGET_INFO_CUDA_DEVICE VALUES (0)")
    connection.executemany(
        "INSERT INTO ENUM_CUDA_MEMCPY_OPER VALUES (?, ?)",
        [(1, "Host-to-Device"), (2, "Device-to-Host")],
    )
    connection.execute(
        "INSERT INTO CUPTI_ACTIVITY_KIND_KERNEL VALUES (300, 400, 0, 1, ?, 1)",
        (global_pid,),
    )
    connection.executemany(
        "INSERT INTO CUPTI_ACTIVITY_KIND_MEMCPY VALUES (?, ?, 0, 1, ?, 8192, ?)",
        [(250, 260, global_pid, 1), (650, 660, global_pid, 2)],
    )
    connection.execute(
        "INSERT INTO CUPTI_ACTIVITY_KIND_RUNTIME VALUES (160, 170, ?, 1, 2, 0)",
        (global_tid,),
    )
    connection.executemany(
        "INSERT INTO NVTX_EVENTS VALUES (?, ?, ?, NULL, ?)",
        [
            (100, 1000, "P0P2V2_PROCESS_START|call_id=1", global_tid),
            (150, 900, "P0P2V2_DEVICE_ALLOCATION_SCOPE|call_id=2", global_tid),
            (200, 700, "pressure.project|call_id=3", global_tid),
            (800, 850, "P0P2V2_FINAL_SYNC_END|call_id=4", global_tid),
        ],
    )
    connection.commit()
    connection.close()

    stats = {name: root / f"{name}.csv" for name in REQUIRED_STATS}
    _write_csv(stats["cuda_api_sum"], "Num Calls,Name", ["1,cudaMalloc"])
    _write_csv(stats["cuda_gpu_kern_sum"], "Instances,Name", ["1,synthetic"])
    _write_csv(stats["cuda_gpu_mem_time_sum"], "Count,Operation", ["2,memcpy"])
    _write_csv(stats["cuda_gpu_mem_size_sum"], "Count,Operation", ["2,memcpy"])
    exact = root / "exact.csv"
    _write_csv(
        exact,
        "Direction,Count,Total Bytes,Total Time (ns)",
        ["Device-to-Host,1,8192,10", "Host-to-Device,1,8192,10"],
    )
    return {
        "report_path": report,
        "sqlite_path": database,
        "stats_paths": stats,
        "exact_mem_path": exact,
        "target_pid": target_pid,
        "expected_pressure_transfers": [
            {
                "call_id": 3,
                "expected_h2d_size_multiset": [8192],
                "expected_d2h_size_multiset": [8192],
            }
        ],
    }


class NsysAuditTests(unittest.TestCase):
    def test_synthetic_nsys_positive_and_negative_gates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            arguments = _nsys_fixture(Path(temporary))
            self.assertTrue(audit_nsys_evidence(**arguments)["gate_pass"])

        with tempfile.TemporaryDirectory() as temporary:
            arguments = _nsys_fixture(Path(temporary))
            connection = sqlite3.connect(arguments["sqlite_path"])
            connection.execute("UPDATE CUPTI_ACTIVITY_KIND_KERNEL SET end = 950")
            connection.commit()
            connection.close()
            report = audit_nsys_evidence(**arguments)
            self.assertFalse(report["gate_pass"])
            self.assertFalse(report["gates"]["gpu_activity_covered_through_final_sync"])

        with tempfile.TemporaryDirectory() as temporary:
            arguments = _nsys_fixture(Path(temporary))
            _write_csv(
                arguments["exact_mem_path"],
                "Direction,Count,Total Bytes,Total Time (ns)",
                ["Device-to-Host,1,8192,10", "Host-to-Device,2,16384,20"],
            )
            report = audit_nsys_evidence(**arguments)
            self.assertFalse(report["gate_pass"])
            self.assertFalse(
                report["gates"]["exact_direction_count_bytes_time_matches_sqlite"]
            )

        with tempfile.TemporaryDirectory() as temporary:
            arguments = _nsys_fixture(Path(temporary))
            connection = sqlite3.connect(arguments["sqlite_path"])
            connection.execute("DROP TABLE CUPTI_ACTIVITY_KIND_KERNEL")
            connection.commit()
            connection.close()
            with self.assertRaises(P0PerformanceError):
                audit_nsys_evidence(**arguments)


class TelemetryAndMemoryTests(unittest.TestCase):
    def test_telemetry_coverage_truncation_and_gap(self) -> None:
        second = 1_000_000_000
        passing = telemetry_coverage(
            [index * second for index in range(11)],
            target_start_ns=0,
            target_end_ns=10 * second,
        )
        self.assertTrue(passing["gate_pass"])
        truncated = telemetry_coverage(
            [index * second for index in range(2, 9)],
            target_start_ns=0,
            target_end_ns=10 * second,
        )
        self.assertFalse(truncated["gate_pass"])
        gap = telemetry_coverage(
            [0, second, 4 * second, 10 * second],
            target_start_ns=0,
            target_end_ns=10 * second,
        )
        self.assertFalse(gap["gate_pass"])
        self.assertGreater(gap["maximum_gap_s"], 2.0)

    def test_pid_reuse_identity_and_96c_environment_rejection(self) -> None:
        expected = ProcessSample(7, 100, 5, "python", "/usr/bin/python")
        self.assertTrue(verify_pid_lifetime(expected, [expected, expected]))
        reused = ProcessSample(7, 101, 5, "python", "/usr/bin/python")
        self.assertFalse(verify_pid_lifetime(expected, [expected, reused]))
        snapshot = {
            "online_logical_cpus": 32,
            "governors": {f"cpu{index}": "powersave" for index in range(32)},
            "ac_online": {"ACAD": 1},
            "tctl_sources": [
                {"chip": "k10temp", "label": "Tctl", "temperature_c": 96.0}
            ],
            "gpu": {
                "gpu": {"temperature.gpu": "68"},
                "compute_processes": [],
            },
        }
        report = evaluate_instantaneous_environment(snapshot)
        self.assertFalse(report["gate_pass"])
        self.assertFalse(report["gates"]["cpu_tctl_at_most_90_c"])

    def test_ticket_machine_identity_rejects_each_governed_drift(self) -> None:
        expected: dict[str, Any] = {
            "host_policy": {
                "governors": {"cpu0": "powersave"},
                "observed_affinity": [0, 1],
                "cpu_model": "synthetic-cpu",
                "ac_online": {"ACAD": 1},
            },
            "nvidia_smi_gpu_identity": {
                "uuid": "GPU-X",
                "driver_version": "580.1",
            },
            "gpu_python_and_cuda": {
                "cuda_runtime_version": 13020,
                "gpu_python_realpath": "/usr/bin/python3.12",
                "python_version": "3.12.3",
                "packages": {"numpy": "2", "scipy": "1", "cupy-cuda13x": "14"},
                "numerical_thread_policy": {"selected_threads": 2},
            },
        }
        drifts: dict[str, tuple[str, str, str | None, Any]] = {
            "governor": ("host_policy", "governors", "cpu0", "performance"),
            "affinity": ("host_policy", "observed_affinity", None, [0]),
            "cpu_model": ("host_policy", "cpu_model", None, "other"),
            "ac": ("host_policy", "ac_online", "ACAD", 0),
            "gpu_uuid": (
                "nvidia_smi_gpu_identity",
                "uuid",
                None,
                "GPU-Y",
            ),
            "cuda_runtime": (
                "gpu_python_and_cuda",
                "cuda_runtime_version",
                None,
                13010,
            ),
            "gpu_python": (
                "gpu_python_and_cuda",
                "gpu_python_realpath",
                None,
                "/tmp/python",
            ),
            "python": (
                "gpu_python_and_cuda",
                "python_version",
                None,
                "3.13.0",
            ),
            "thread_policy": (
                "gpu_python_and_cuda",
                "numerical_thread_policy",
                "selected_threads",
                1,
            ),
        }
        self.assertTrue(audit_ticket_machine_identity(expected, expected)["gate_pass"])
        for name, (section, field, child, value) in drifts.items():
            observed = copy.deepcopy(expected)
            if child is None:
                observed[section][field] = value
            else:
                observed[section][field][child] = value
            with self.subTest(name=name):
                report = audit_ticket_machine_identity(expected, observed)
                self.assertFalse(report["gate_pass"])
                self.assertTrue(report["mismatches"])

    def test_full_30s_environment_gate_and_short_window_rejection(self) -> None:
        snapshot = {
            "online_logical_cpus": 32,
            "governors": {f"cpu{index}": "powersave" for index in range(32)},
            "ac_online": {"ACAD": 1},
            "tctl_sources": [
                {"chip": "k10temp", "label": "Tctl", "temperature_c": 60.0}
            ],
            "gpu": {
                "gpu": {"temperature.gpu": "50"},
                "compute_processes": [],
            },
        }
        processes = {7: ProcessSample(7, 1, 0, "orchestrator", None)}
        for seconds, expected_pass in ((30, True), (29, False)):
            clock = iter((0, seconds * 1_000_000_000))
            next_clock = clock.__next__
            report = full_environment_gate(
                background_duration_s=float(seconds),
                sleep=lambda _duration: None,
                environment_capture=lambda: copy.deepcopy(snapshot),
                process_capture=lambda: dict(processes),
                monotonic_ns=next_clock,
            )
            with self.subTest(seconds=seconds):
                self.assertIs(report["gate_pass"], expected_pass)
                self.assertIs(
                    report["background_gates"]["background_window_at_least_30_s"],
                    expected_pass,
                )

    def test_background_lifetime_monitor_detects_cpu_and_compute_pollution(self) -> None:
        clock_ticks = os.sysconf("SC_CLK_TCK")

        def payload(target_ticks: int, other_ticks: int = 0) -> list[dict[str, Any]]:
            rows = [
                asdict(ProcessSample(7, 1, target_ticks, "worker", "/worker", 99)),
                asdict(ProcessSample(50, 1, 0, "monitor", "/monitor", 99)),
            ]
            if other_ticks:
                rows.append(
                    asdict(
                        ProcessSample(
                            80,
                            1,
                            other_ticks,
                            "python competing.py",
                            "/usr/bin/python",
                            1,
                        )
                    )
                )
            return rows

        def records(*, polluted: bool) -> list[dict[str, Any]]:
            samples = []
            for index in range(3):
                target_ticks = index * clock_ticks
                other_ticks = index * 10 if polluted else 0
                busy = index * (clock_ticks + (50 if polluted else 0))
                samples.append(
                    {
                        "record_type": "sample",
                        "kind": "background",
                        "monotonic_ns": index * 1_000_000_000,
                        "target_pid": 7,
                        "target_start_ticks": 1,
                        "value": {
                            "processes": payload(target_ticks, other_ticks),
                            "system_cpu": {
                                "total_ticks": 1000 + index * 3200,
                                "busy_ticks": 100 + busy,
                            },
                        },
                    }
                )
            return [
                {
                    "record_type": "monitor_start",
                    "kind": "background",
                    "monitor_pid": 50,
                    "target_pid": 7,
                    "target_start_ticks": 1,
                    "monotonic_ns": 0,
                    "excluded_pids": [7, 99],
                },
                *samples,
                {
                    "record_type": "monitor_summary",
                    "kind": "background",
                    "monitor_pid": 50,
                    "target_pid": 7,
                    "target_start_ticks": 1,
                    "first_monotonic_ns": 0,
                    "last_monotonic_ns": 2_000_000_000,
                    "sample_count": 3,
                    "stop_file_seen": True,
                    "target_alive_at_stop": False,
                },
            ]

        passing = audit_background_records(
            records(polluted=False),
            target_pid=7,
            target_start_ticks=1,
            target_start_ns=0,
            target_end_ns=2_000_000_000,
            allowed_pids={7, 99, 50},
        )
        self.assertTrue(passing["gate_pass"])
        failing = audit_background_records(
            records(polluted=True),
            target_pid=7,
            target_start_ticks=1,
            target_start_ns=0,
            target_end_ns=2_000_000_000,
            allowed_pids={7, 99, 50},
        )
        self.assertFalse(failing["gate_pass"])
        self.assertTrue(failing["other_compute_processes"])

    def test_cuda_worker_lifetime_allows_nsys_finalization_lag(self) -> None:
        second = 1_000_000_000
        passing = campaign_module.audit_worker_launcher_lifetimes(
            mode="cuda",
            worker_start_ns=second,
            worker_end_ns=10 * second,
            launcher_start_ns=0,
            launcher_end_ns=15 * second,
            launcher_exit_code=0,
        )
        self.assertTrue(passing["gate_pass"])
        self.assertEqual(
            passing["launcher"]["post_worker_finalization_lag_s"], 5.0
        )
        nonzero = campaign_module.audit_worker_launcher_lifetimes(
            mode="cuda",
            worker_start_ns=second,
            worker_end_ns=10 * second,
            launcher_start_ns=0,
            launcher_end_ns=15 * second,
            launcher_exit_code=2,
        )
        self.assertFalse(nonzero["gate_pass"])

    def test_native_allocator_known_exit_overflow_and_device_crosscheck(self) -> None:
        build_native_artifacts()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for mode, expected_exit in (("known", 0), ("exit7", 7)):
                output = root / f"{mode}.json"
                completed = run_probe(mode, output_path=output)
                self.assertEqual(completed.returncode, expected_exit)
                payload = strict_json(output)
                self.assertTrue(
                    audit_known_probe(output, expected_pid=int(payload["pid"]))[
                        "gate_pass"
                    ]
                )
            overflow = root / "overflow.json"
            completed = run_probe("overflow", output_path=overflow)
            self.assertEqual(completed.returncode, 0)
            payload = strict_json(overflow)
            self.assertTrue(
                audit_observer_payload(
                    overflow,
                    expected_pid=int(payload["pid"]),
                    allow_overflow=True,
                )["gate_pass"]
            )

        recorder = {
            "underlying_device_allocation_count": 2,
            "device_used_high_water_bytes": 8_000_000,
            "pool_total_high_water_bytes": 7_500_000,
        }
        passing = device_allocation_crosscheck(
            recorder,
            nsys_allocation_api_counts={"cudaMalloc": 2},
            nvidia_smi_peak_bytes=8_000_000,
        )
        self.assertTrue(passing["gate_pass"])
        failing = device_allocation_crosscheck(
            recorder,
            nsys_allocation_api_counts={"cudaMalloc": 3},
            nvidia_smi_peak_bytes=8_000_000,
        )
        self.assertFalse(failing["gate_pass"])
        self.assertFalse(failing["gates"]["no_unmatched_device_allocation"])


class NumericalAndTransactionTests(unittest.TestCase):
    def test_analysis_snapshot_binds_claim_commit_and_parser_blobs(self) -> None:
        contract_hash = "f" * 64
        files = {
            "analyser.py": {"sha256": "a" * 64, "git_blob_sha": "b" * 40},
            analysis_module.relative(analysis_module.CONTRACT_PATH): {
                "sha256": contract_hash,
                "git_blob_sha": "e" * 40,
            },
        }
        source = {"sha256": "c" * 64}
        observed = {
            "symbolic_head": "refs/heads/main",
            "execution_commit": "d" * 40,
            "worktree_clean": True,
            "contract_sha256": contract_hash,
            "implementation_files": files,
            "formal_source_tree": source,
            "preclaim_machine_identity": {"machine": "exact"},
            "toolchain_binding": {"machine": "exact"},
        }
        claim = {
            "authorization_child_commit": "d" * 40,
            "implementation_files": files,
            "formal_source_tree": source,
        }
        with patch.object(
            analysis_module,
            "execution_binding",
            return_value=observed,
        ):
            self.assertTrue(analysis_module.analysis_git_snapshot(claim)["gate_pass"])
        for field, value in (
            ("authorization_child_commit", "e" * 40),
            ("implementation_files", {"analyser.py": {"sha256": "0" * 64}}),
        ):
            malformed = {**claim, field: value}
            with (
                self.subTest(field=field),
                patch.object(
                    analysis_module,
                    "execution_binding",
                    return_value=observed,
                ),
                self.assertRaises(P0PerformanceError),
            ):
                analysis_module.analysis_git_snapshot(malformed)

    def test_analysis_stale_staging_is_retained_with_explicit_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary) / "analysis"
            transaction = DirectoryPublicationTransaction(parent, "compact_evidence")
            staging = transaction.begin()
            (staging / "retained.txt").write_text("retained\n", encoding="utf-8")
            with self.assertRaises(P0PerformanceError) as raised:
                DirectoryPublicationTransaction(parent, "compact_evidence").begin()
            with patch.multiple(
                analysis_module,
                OUTPUT_PARENT=parent,
                OUTPUT_ROOT=parent / "compact_evidence",
                ANALYSIS_FAILURE_PATH=parent / "analysis_failure.json",
            ):
                failure = analysis_module.publish_analysis_failure(raised.exception)
            self.assertTrue(Path(failure["path"]).is_file())
            self.assertEqual(failure["record"]["retained_staging"], [staging.name])
            self.assertTrue((staging / "retained.txt").is_file())

    def test_analysis_rename_failure_retains_terminal_staging_and_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary) / "analysis"
            transaction = DirectoryPublicationTransaction(parent, "compact_evidence")
            staging = transaction.begin()
            (staging / "report.json").write_text("{}\n", encoding="utf-8")
            with (
                patch.object(
                    transaction_module,
                    "_rename_directory_noreplace",
                    side_effect=P0PerformanceError("synthetic analysis rename failure"),
                ),
                self.assertRaises(P0PerformanceError) as raised,
            ):
                transaction.publish_terminal(
                    "publication_complete.json",
                    {"schema_version": 2, "status": "COMPLETE", "gate_pass": True},
                )
            with patch.multiple(
                analysis_module,
                OUTPUT_PARENT=parent,
                OUTPUT_ROOT=parent / "compact_evidence",
                ANALYSIS_FAILURE_PATH=parent / "analysis_failure.json",
            ):
                failure = analysis_module.publish_analysis_failure(raised.exception)
            self.assertTrue((staging / "publication_complete.json").is_file())
            self.assertTrue(Path(failure["path"]).is_file())
            self.assertFalse((parent / "compact_evidence").exists())

    def test_numerical_hash_is_layout_stable_and_metadata_files_are_excluded(
        self,
    ) -> None:
        import numpy as np

        baseline = np.arange(12, dtype=np.float64).reshape(3, 4)
        c_identity = canonical_arrays_identity({"field": baseline})
        f_identity = canonical_arrays_identity({"field": np.asfortranarray(baseline)})
        self.assertEqual(c_identity["sha256"], f_identity["sha256"])
        changed = baseline.copy()
        changed[0, 0] = 1.0
        self.assertNotEqual(
            c_identity["sha256"],
            canonical_arrays_identity({"field": changed})["sha256"],
        )
        with self.assertRaises(P0PerformanceError):
            canonical_arrays_identity({"bad": np.asarray([np.nan])})

        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / "archive"
            archive.mkdir()
            (archive / "config.json").write_text('{"duration":1}\n', encoding="utf-8")
            (archive / "result.json").write_text('{"status":"ok"}\n', encoding="utf-8")
            np.savez(
                archive / "arrays.npz",
                time_s=np.asarray([0.0, 0.001, 0.002], dtype=np.float64),
                field=baseline,
            )
            (archive / "manifest.json").write_text("first\n", encoding="utf-8")
            sequence = [
                {"trial_index": 0, "status": "ACCEPTED", "dt_s": 0.001}
            ]
            first = archive_numerical_payload(
                archive, execution_sequence=sequence
            )
            (archive / "manifest.json").write_text("changed timing\n", encoding="utf-8")
            second = archive_numerical_payload(
                archive, execution_sequence=sequence
            )
            self.assertEqual(first["sha256"], second["sha256"])
            self.assertTrue(compare_payloads([first, second])["gate_pass"])
            mismatch = archive_numerical_payload(
                archive,
                execution_sequence=[
                    {"trial_index": 0, "status": "ACCEPTED", "dt_s": 0.0005}
                ],
            )
            self.assertFalse(compare_payloads([first, mismatch])["gate_pass"])

    def test_sample_transaction_no_replace_and_symlink_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "raw"
            transaction = SampleTransaction(root, "WARMUP_B1")
            staging = transaction.begin()
            (staging / "evidence.txt").write_text("retained\n", encoding="utf-8")
            transaction.publish_success({"gate_pass": True})
            self.assertTrue((root / "WARMUP_B1" / "sample_manifest.json").is_file())
            with self.assertRaises(P0PerformanceError):
                SampleTransaction(root, "WARMUP_B1").begin()

        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            target = base / "target"
            target.mkdir()
            link = base / "raw"
            link.symlink_to(target, target_is_directory=True)
            with self.assertRaises(P0PerformanceError):
                SampleTransaction(link, "WARMUP_B1").begin()

    def test_directory_publication_writes_terminal_last_and_never_replaces(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary) / "evidence"
            transaction = DirectoryPublicationTransaction(parent, "compact")
            staging = transaction.begin()
            (staging / "report.txt").write_text("complete\n", encoding="utf-8")
            published = transaction.publish_terminal(
                "publication_complete.json",
                {"schema_version": 2, "status": "COMPLETE", "gate_pass": True},
            )
            self.assertTrue((published / "report.txt").is_file())
            self.assertTrue(
                (published / "publication_complete.json").is_file()
            )
            with self.assertRaises(P0PerformanceError):
                DirectoryPublicationTransaction(parent, "compact").begin()

    def test_sample_publication_failure_retains_staging_and_no_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "raw"
            transaction = SampleTransaction(root, "P0P2V2_WARMUP_B1")
            staging = transaction.begin()
            (staging / "evidence.txt").write_text("retained\n", encoding="utf-8")
            with (
                patch.object(
                    transaction_module,
                    "_rename_directory_noreplace",
                    side_effect=P0PerformanceError("synthetic publication failure"),
                ),
                self.assertRaises(P0PerformanceError),
            ):
                transaction.publish_success({"gate_pass": True})
            self.assertTrue(staging.is_dir())
            self.assertTrue((staging / "sample_manifest.json").is_file())
            self.assertFalse((root / "P0P2V2_WARMUP_B1").exists())

    def test_monitor_start_and_stop_failures_are_observable(self) -> None:
        class ExitedMonitor:
            pid = 12345
            returncode = 2

            def poll(self) -> int:
                return 2

            def communicate(
                self, timeout: float | None = None
            ) -> tuple[bytes, bytes]:
                del timeout
                return b"", b"synthetic monitor failure"

        worker = {"pid": 7, "process_start_ticks": 11}
        with tempfile.TemporaryDirectory() as temporary:
            staging = Path(temporary)
            with self.assertRaises(P0PerformanceError):
                campaign_module._wait_for_monitor_starts(
                    staging,
                    cast(
                        Any,
                        [
                            ExitedMonitor(),
                            ExitedMonitor(),
                            ExitedMonitor(),
                            ExitedMonitor(),
                        ],
                    ),
                    worker,
                    timeout_s=0.01,
                )
            (staging / "monitors.stop").touch()
            with self.assertRaises(P0PerformanceError):
                campaign_module._stop_monitors(
                    staging, cast(Any, [ExitedMonitor()])
                )


if __name__ == "__main__":
    unittest.main()
