#!/usr/bin/env python3
"""Execute only synthetic/zero-step P0-P harness qualification."""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from p0_performance_contract import (
    GPU_PYTHON,
    HERE,
    NSYS_PATH,
    RAW_ROOT,
    STATIC_ROOT,
    P0PerformanceError,
    atomic_json,
    evidence_identity,
    formal_source_identity,
    frozen_child_environment,
    load_authorization,
    load_contract,
    numerical_quality_gates,
    strict_json,
)
from p0p_allocation import (
    audit_known_probe,
    audit_observer_payload,
    build_native_artifacts,
    run_probe,
)
from p0p_authorization import FORBIDDEN_SELF_FIELDS, TICKET_PATH
from p0p_consumption import CAMPAIGN_LOCK_PATH
from p0p_nsys import (
    REQUIRED_STATS,
    audit_nsys_evidence,
    export_commands,
    profile_command,
)
from p0p_telemetry import evaluate_instantaneous_environment
from run_p0_performance_campaign import zero_step_preflight

PROBE = HERE / "p0p_capability_probe.py"
WORKER = HERE / "run_p0_performance_sample.py"
ORCHESTRATOR = HERE / "run_p0_performance_campaign.py"
SYNTHETIC_TESTS = (
    HERE / "tests" / "test_p0p_authorization_and_consumption.py",
    HERE / "tests" / "test_p0_performance_harness.py",
)


def _write_new_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
        0o600,
    )
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())


def _run(
    command: Sequence[str],
    *,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    completed = subprocess.run(
        list(command),
        check=False,
        capture_output=True,
        text=True,
        env=dict(environment) if environment is not None else None,
    )
    return {
        "command": list(command),
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def authorization_denial_probe(root: Path) -> dict[str, Any]:
    environment = dict(os.environ)
    environment["PYTHONPROFILEIMPORTTIME"] = "1"
    worker = _run([str(GPU_PYTHON), str(WORKER)], environment=environment)
    orchestrator = _run(
        [sys.executable, str(ORCHESTRATOR), "--execute"],
        environment=environment,
    )
    combined = (
        worker["stdout"]
        + worker["stderr"]
        + orchestrator["stdout"]
        + orchestrator["stderr"]
    )
    forbidden_imports = [
        name for name in ("airvortex", "numpy", "scipy", "cupy") if name in combined
    ]
    _write_new_text(root / "worker_denial.stdout", worker["stdout"])
    _write_new_text(root / "worker_denial.stderr", worker["stderr"])
    _write_new_text(root / "orchestrator_denial.stdout", orchestrator["stdout"])
    _write_new_text(root / "orchestrator_denial.stderr", orchestrator["stderr"])
    gates = {
        "worker_nonzero_exit": worker["exit_code"] != 0,
        "orchestrator_nonzero_exit": orchestrator["exit_code"] != 0,
        "worker_and_orchestrator_exact_denial_status": all(
            "HF3D_P0_P_DYNAMIC_SAMPLES_NOT_AUTHORIZED_PENDING_PRO_STATIC_REVIEW"
            in result["stdout"] + result["stderr"]
            for result in (worker, orchestrator)
        ),
        "no_numerical_stack_imported": not forbidden_imports,
        "performance_raw_root_not_created": not RAW_ROOT.exists()
        or (RAW_ROOT.is_dir() and not any(RAW_ROOT.iterdir())),
        "campaign_lock_not_created": not CAMPAIGN_LOCK_PATH.exists()
        and not CAMPAIGN_LOCK_PATH.is_symlink(),
    }
    return {
        "gate_pass": all(gates.values()),
        "gates": gates,
        "worker_exit_code": worker["exit_code"],
        "orchestrator_exit_code": orchestrator["exit_code"],
        "forbidden_imports": forbidden_imports,
    }


def synthetic_unit_tests(root: Path) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    total_tests = 0
    for index, test_path in enumerate(SYNTHETIC_TESTS):
        result = _run([sys.executable, str(test_path), "-v"])
        _write_new_text(root / f"unit_tests_{index}.stdout", result["stdout"])
        _write_new_text(root / f"unit_tests_{index}.stderr", result["stderr"])
        match = re.search(r"Ran ([0-9]+) tests?", result["stderr"])
        observed_count = int(match.group(1)) if match is not None else 0
        total_tests += observed_count
        results.append(
            {
                "test_path": str(test_path.relative_to(HERE)),
                "exit_code": result["exit_code"],
                "observed_test_count": observed_count,
                "unittest_ok_marker": "\nOK\n" in result["stderr"],
                "failed_marker_absent": "FAILED" not in result["stderr"],
            }
        )
    gates = {
        "exact_two_synthetic_test_programs": len(results) == 2,
        "all_test_programs_exit_zero": all(
            result["exit_code"] == 0 for result in results
        ),
        "all_test_programs_report_ok": all(
            result["unittest_ok_marker"] and result["failed_marker_absent"]
            for result in results
        ),
        "at_least_30_tests_executed": total_tests >= 30,
    }
    return {
        "gate_pass": all(gates.values()),
        "gates": gates,
        "total_tests": total_tests,
        "results": results,
        "hf3d_time_steps_advanced": 0,
    }


def worker_ast_gate() -> dict[str, Any]:
    tree = ast.parse(WORKER.read_text(encoding="utf-8"), filename=str(WORKER))
    authorization_line: int | None = None
    numerical_import_lines: list[int] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "load_authorization"
        ):
            authorization_line = min(authorization_line or node.lineno, node.lineno)
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            modules = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            else:
                modules = [node.module or ""]
            if any(
                module == name or module.startswith(name + ".")
                for module in modules
                for name in ("airvortex", "numpy", "scipy", "cupy")
            ):
                numerical_import_lines.append(node.lineno)
    gates = {
        "authorization_call_present": authorization_line is not None,
        "numerical_imports_present": bool(numerical_import_lines),
        "authorization_precedes_numerical_imports": authorization_line is not None
        and bool(numerical_import_lines)
        and authorization_line < min(numerical_import_lines),
    }
    return {
        "gate_pass": all(gates.values()),
        "gates": gates,
        "authorization_line": authorization_line,
        "first_numerical_import_line": min(numerical_import_lines)
        if numerical_import_lines
        else None,
    }


def allocation_probes(root: Path) -> dict[str, Any]:
    build = build_native_artifacts()
    outcomes: dict[str, Any] = {}
    for mode, expected_exit in (("known", 0), ("exit7", 7), ("overflow", 0)):
        output = root / f"allocator_{mode}.json"
        completed = run_probe(mode, output_path=output)
        if mode in {"known", "exit7"}:
            audit = audit_known_probe(
                output,
                expected_pid=completed.args and _payload_pid(output),
                logical_path=STATIC_ROOT / output.name,
            )
        else:
            audit = audit_observer_payload(
                output,
                expected_pid=_payload_pid(output),
                expected_calls={"calloc": 1},
                allow_overflow=True,
                logical_path=STATIC_ROOT / output.name,
            )
        audit["expected_exit_code"] = expected_exit
        audit["observed_exit_code"] = completed.returncode
        audit["exit_code_gate"] = completed.returncode == expected_exit
        audit["gate_pass"] = audit["gate_pass"] and audit["exit_code_gate"]
        outcomes[mode] = audit
    return {
        "gate_pass": build["gate_pass"]
        and all(outcome["gate_pass"] for outcome in outcomes.values()),
        "build": build,
        "probes": outcomes,
    }


def _payload_pid(path: Path) -> int:
    value = strict_json(path).get("pid")
    if not isinstance(value, int):
        raise P0PerformanceError("allocation probe did not record an integer PID")
    return value


def numerical_equivalence_probes(root: Path) -> dict[str, Any]:
    results: dict[str, Any] = {}
    environment = frozen_child_environment(os.environ)
    for mode in ("uninstrumented", "semantic", "device-memory"):
        output = root / f"numerical_{mode}.json"
        completed = _run(
            [
                str(GPU_PYTHON),
                str(PROBE),
                "--mode",
                mode,
                "--output",
                str(output),
            ],
            environment=environment,
        )
        if completed["exit_code"] != 0:
            raise P0PerformanceError(
                f"numerical equivalence probe failed ({mode}): {completed['stderr']}"
            )
        results[mode] = strict_json(output)
    hashes = {mode: result["numerical_sha256"] for mode, result in results.items()}
    device = results["device-memory"]["device_allocation_recorder"]
    toolchains = [result["toolchain_identity"] for result in results.values()]
    gates = {
        "all_numerical_hashes_identical": len(set(hashes.values())) == 1,
        "semantic_ranges_recorded": bool(results["semantic"]["semantic_ranges"]),
        "device_requests_recorded": int(device["pool_request_count"]) > 0,
        "device_peak_recorded": int(device["device_used_high_water_bytes"]) > 0,
        "toolchain_identity_stable_across_modes": all(
            value == toolchains[0] for value in toolchains
        ),
        "hf3d_steps_zero": all(
            result["hf3d_time_steps_advanced"] == 0 for result in results.values()
        ),
    }
    return {
        "gate_pass": all(gates.values()),
        "gates": gates,
        "hashes": hashes,
        "results": results,
        "toolchain_identity": toolchains[0],
    }


def capability_manifest(
    *,
    native: Mapping[str, Any],
    numerical: Mapping[str, Any],
    current_preflight: Mapping[str, Any],
) -> dict[str, Any]:
    nsys = _run([str(NSYS_PATH), "--version"])
    if nsys["exit_code"] != 0:
        raise P0PerformanceError("nsys version probe failed")
    version_line = (nsys["stdout"] or nsys["stderr"]).strip().splitlines()
    if len(version_line) != 1 or "2026.2.1" not in version_line[0]:
        raise P0PerformanceError("nsys version differs from qualified 2026.2.1")
    environment = current_preflight["environment"]
    snapshot = environment["snapshot"]
    gpu = snapshot["gpu"]["gpu"]
    toolchain = numerical["toolchain_identity"]
    if not isinstance(toolchain, Mapping):
        raise P0PerformanceError("numerical toolchain identity is unavailable")
    return {
        "schema_version": 1,
        "status": "HF3D_P0P_ZERO_STEP_TOOLCHAIN_CAPABILITY_BOUND",
        "gpu_python_and_cuda": dict(toolchain),
        "nsys": {
            "path": str(NSYS_PATH),
            "version_line": version_line[0],
            "qualified_trace": "cuda,nvtx",
            "sample": "none",
            "cpuctxsw": "none",
        },
        "native_compiler": {
            "version_line": native["build"]["compiler_first_line"],
            "build_flags": native["build"]["build_flags"],
        },
        "nvidia_smi_gpu_identity": {
            "name": gpu["name"],
            "uuid": gpu["uuid"],
            "driver_version": gpu["driver_version"],
        },
        "host_policy": {
            "expected_online_logical_cpus": 32,
            "formal_selected_blas_threads": 2,
            "omp_dynamic": "FALSE",
        },
    }


def nsys_probe(root: Path) -> dict[str, Any]:
    output = root / "nsys_probe_result.json"
    base = root / "nsys_probe"
    environment = frozen_child_environment(os.environ)
    command = profile_command(
        output_base=base,
        target_command=[
            str(GPU_PYTHON),
            str(PROBE),
            "--mode",
            "cuda-trace",
            "--output",
            str(output),
        ],
    )
    completed = _run(command, environment=environment)
    _write_new_text(root / "nsys_profile.stdout", completed["stdout"])
    _write_new_text(root / "nsys_profile.stderr", completed["stderr"])
    if completed["exit_code"] != 0:
        raise P0PerformanceError("minimal nsys profile failed")
    report = root / "nsys_probe.nsys-rep"
    sqlite_path = root / "nsys_probe.sqlite"
    bases = {name: root / name for name in REQUIRED_STATS}
    bases["p0p_cuda_mem_exact"] = root / "p0p_cuda_mem_exact"
    export_results = []
    for export in export_commands(
        report_path=report,
        sqlite_path=sqlite_path,
        report_output_bases=bases,
    ):
        result = _run(export)
        export_results.append(result)
        if result["exit_code"] != 0:
            raise P0PerformanceError(
                "minimal nsys export/report failed: " + result["stderr"]
            )
    discovered: dict[str, Path] = {}
    for name, report_base in bases.items():
        matches = sorted(root.glob(f"{report_base.name}_*.csv"))
        if len(matches) != 1:
            raise P0PerformanceError(
                f"minimal nsys report output is not unique: {name}"
            )
        discovered[name] = matches[0]
    probe = strict_json(output)
    audit = audit_nsys_evidence(
        report_path=report,
        sqlite_path=sqlite_path,
        stats_paths={name: discovered[name] for name in REQUIRED_STATS},
        exact_mem_path=discovered["p0p_cuda_mem_exact"],
        target_pid=int(probe["pid"]),
        expected_pressure_transfers=probe["expected_pressure_transfers"],
        logical_root=STATIC_ROOT,
    )
    audit["profile_command"] = command
    audit["export_commands"] = [result["command"] for result in export_results]
    return audit


def synthetic_invalid_environment_gate() -> dict[str, Any]:
    snapshot = {
        "online_logical_cpus": 32,
        "governors": {f"cpu{index}": "powersave" for index in range(32)},
        "ac_online": {"ACAD": 1},
        "tctl_sources": [{"chip": "k10temp", "label": "Tctl", "temperature_c": 96.0}],
        "gpu": {
            "gpu": {"temperature.gpu": "68"},
            "compute_processes": [],
        },
    }
    audit = evaluate_instantaneous_environment(snapshot)
    audit["expected_rejection"] = not audit["gate_pass"]
    return audit


def static_markdown(report: Mapping[str, Any]) -> str:
    gates = report["gates"]
    lines = [
        "# HF3D P0-P 零时间步实现静态审计",
        "",
        f"总体状态：`{'PASS' if report['gate_pass'] else 'FAIL'}`。",
        "",
        "| 审计项 | 结果 |",
        "|---|---:|",
    ]
    lines.extend(
        f"| {name} | {'PASS' if passed else 'FAIL'} |" for name, passed in gates.items()
    )
    lines.extend(
        [
            "",
            "本审计只运行合成能力探针；HF3D 时间步、warm-up、scored 和 profile 样本均为 0。",
            "",
            "动态 P0-P 仍为 `NOT_AUTHORIZED_PENDING_PRO_STATIC_REVIEW`；不得据此开跑。",
            "",
            "当前结论不包含候选加速、P1、数值资格、物理验证、实验验证或装置预测。",
            "",
        ]
    )
    return "\n".join(lines)


def audit(output_root: Path) -> dict[str, Any]:
    if output_root.exists() or output_root.is_symlink():
        raise P0PerformanceError("static audit output root must not pre-exist")
    output_root.mkdir(parents=True)
    root_before = RAW_ROOT.exists()
    contract = load_contract()
    quality = numerical_quality_gates()
    authorization = load_authorization(require_dynamic=False)
    evidence = evidence_identity()
    source_before = formal_source_identity()
    denial = authorization_denial_probe(output_root)
    ast_gate = worker_ast_gate()
    unit_tests = synthetic_unit_tests(output_root)
    native = allocation_probes(output_root)
    numerical = numerical_equivalence_probes(output_root)
    nsys = nsys_probe(output_root)
    current_preflight = zero_step_preflight()
    capabilities = capability_manifest(
        native=native,
        numerical=numerical,
        current_preflight=current_preflight,
    )
    atomic_json(output_root / "p0p_capability_manifest.json", capabilities)
    synthetic_environment = synthetic_invalid_environment_gate()
    source_after = formal_source_identity()
    root_after = RAW_ROOT.exists()
    gates = {
        "contract_and_quality_bound": bool(contract) and bool(quality),
        "authorization_default_denial": authorization.get("dynamic_samples_authorized")
        is False,
        "actual_parent_ticket_absent": not TICKET_PATH.exists()
        and not TICKET_PATH.is_symlink(),
        "self_referential_authorization_fields_absent": not (
            FORBIDDEN_SELF_FIELDS & set(authorization)
        ),
        "p0d_and_pro_evidence_bound": evidence["gate_pass"],
        "worker_denies_before_numerical_imports": denial["gate_pass"]
        and ast_gate["gate_pass"],
        "synthetic_parent_ticket_and_harness_tests": unit_tests["gate_pass"],
        "native_allocator_build_and_probes": native["gate_pass"],
        "instrumentation_numerical_equivalence": numerical["gate_pass"],
        "nsys_minimal_trace_and_parsers": nsys["gate_pass"],
        "toolchain_capability_manifest_bound": bool(capabilities),
        "current_zero_step_preflight_did_not_authorize": current_preflight[
            "p0p_dynamic_authorization"
        ]
        == "NOT_AUTHORIZED",
        "synthetic_96c_environment_rejected": synthetic_environment[
            "expected_rejection"
        ],
        "formal_source_unchanged": source_before == source_after,
        "performance_raw_not_created": root_before == root_after
        and (not RAW_ROOT.exists() or not any(RAW_ROOT.iterdir())),
        "hf3d_time_steps_zero": True,
        "performance_samples_started_zero": True,
        "p1_not_implemented": True,
    }
    report = {
        "schema_version": 1,
        "status": "HF3D_P0P_ZERO_STEP_HARNESS_STATIC_AUDIT_PASS"
        if all(gates.values())
        else "HF3D_P0P_ZERO_STEP_HARNESS_STATIC_AUDIT_FAIL",
        "gate_pass": all(gates.values()),
        "gates": gates,
        "authorization": authorization,
        "authorization_denial_probe": denial,
        "worker_ast_gate": ast_gate,
        "synthetic_unit_tests": unit_tests,
        "native_allocator": native,
        "numerical_equivalence": numerical,
        "nsys_capability": nsys,
        "capability_manifest": capabilities,
        "current_zero_step_preflight": current_preflight,
        "synthetic_invalid_environment": synthetic_environment,
        "formal_source_before": source_before,
        "formal_source_after": source_after,
        "counters": {
            "hf3d_time_steps_advanced": 0,
            "warmup_samples_started": 0,
            "scored_samples_started": 0,
            "profile_samples_started": 0,
            "performance_samples_started": 0,
        },
        "p0p_dynamic_authorization": "NOT_AUTHORIZED",
        "p1_implemented": False,
    }
    atomic_json(output_root / "p0p_static_audit.json", report)
    _write_new_text(output_root / "p0p_static_audit.md", static_markdown(report))
    return report


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, default=STATIC_ROOT)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report = audit(args.output_root.resolve())
    except Exception as exc:  # noqa: BLE001 - CLI must fail closed with a concise record.
        print(f"P0-P static audit failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"status": report["status"], "gate_pass": report["gate_pass"]}))
    return 0 if report["gate_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
