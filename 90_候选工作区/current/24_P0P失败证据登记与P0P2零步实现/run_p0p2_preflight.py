#!/usr/bin/env python3
"""Run the unified non-consuming P0-P2 preclaim preflight."""

from __future__ import annotations

import argparse
import os
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import register_v1_failure_evidence as e0_registration
from p0p2_authorization import load_authorization
from p0p2_contract import (
    AUTHORIZATION_PATH,
    BENCHMARKS,
    CONTRACT_PATH,
    DENIED_STATUS,
    EFFECTIVE_CONFIG_ROOT,
    FORMAL_SOURCE,
    GPU_PYTHON,
    LOCK_PATH,
    RAW_ROOT,
    REPOSITORY_ROOT,
    P0P2Error,
    assert_namespace_separation,
    assert_runtime_input_separation,
    load_contract,
    pretty_json_bytes,
    source_config_path,
    strict_json_bytes,
    verify_generated_outputs,
)
from p0p2_environment import BACKGROUND_OBSERVATION_S, full_environment_gate

PROBE_PATH = Path(__file__).resolve().with_name("p0p2_zero_step_probe.py")


def _path_state(path: Path) -> dict[str, Any]:
    exists = path.exists()
    symlink = path.is_symlink()
    if not exists and not symlink:
        return {"exists": False, "is_symlink": False, "kind": "absent"}
    if symlink:
        return {"exists": exists, "is_symlink": True, "kind": "symlink"}
    if path.is_dir():
        return {
            "exists": True,
            "is_symlink": False,
            "kind": "directory",
            "children": sorted(item.name for item in path.iterdir()),
        }
    if path.is_file():
        return {
            "exists": True,
            "is_symlink": False,
            "kind": "file",
            "byte_length": path.stat().st_size,
        }
    return {"exists": True, "is_symlink": False, "kind": "special"}


def side_effect_state() -> dict[str, Any]:
    return {
        "raw_root": _path_state(RAW_ROOT),
        "lock_path": _path_state(LOCK_PATH),
        "claim_path": _path_state(RAW_ROOT / "execution_claim.json"),
        "ledger_path": _path_state(RAW_ROOT / "schedule_ledger.json"),
    }


def _run_probe_subprocess(benchmark: str) -> dict[str, Any]:
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        [
            str(GPU_PYTHON),
            str(PROBE_PATH),
            "--benchmark",
            benchmark,
        ],
        cwd=REPOSITORY_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        timeout=900.0,
    )
    try:
        raw = strict_json_bytes(
            completed.stdout, label=f"fresh-process probe stdout {benchmark}"
        )
    except P0P2Error as exc:
        raw = {
            "schema_version": 1,
            "status": "HF3D_P0P2_EXACT_CASE_ZERO_STEP_PROBE_FAILED_STOPPED",
            "gate_pass": False,
            "benchmark_id": benchmark,
            "message": str(exc),
        }
    if not isinstance(raw, dict):
        raw = {
            "schema_version": 1,
            "status": "HF3D_P0P2_EXACT_CASE_ZERO_STEP_PROBE_FAILED_STOPPED",
            "gate_pass": False,
            "benchmark_id": benchmark,
            "message": "probe output was not one JSON object",
        }
    return {
        "benchmark_id": benchmark,
        "returncode": completed.returncode,
        "stderr_empty": completed.stderr == b"",
        "stderr_utf8": completed.stderr.decode("utf-8", errors="replace"),
        "report": raw,
        "gate_pass": (
            completed.returncode == 0
            and completed.stderr == b""
            and raw.get("gate_pass") is True
            and raw.get("benchmark_id") == benchmark
            and raw.get("hf3d_time_steps_advanced") == 0
        ),
    }


def run_preflight(
    *,
    background_duration_s: float = BACKGROUND_OBSERVATION_S,
    probe_runner: Callable[[str], dict[str, Any]] = _run_probe_subprocess,
    environment_runner: Callable[..., dict[str, Any]] = full_environment_gate,
) -> dict[str, Any]:
    """Evaluate every gate while leaving lock/raw/claim/ledger/sample absent."""

    before = side_effect_state()
    contract = load_contract()
    generated = verify_generated_outputs()
    e0 = e0_registration.check_registration()
    namespace_gates = assert_namespace_separation()
    runtime_input_gates = assert_runtime_input_separation(
        [
            CONTRACT_PATH,
            AUTHORIZATION_PATH,
            FORMAL_SOURCE,
            *(source_config_path(benchmark) for benchmark in BENCHMARKS),
            *(
                EFFECTIVE_CONFIG_ROOT / f"P0P2_{benchmark}.json"
                for benchmark in BENCHMARKS
            ),
        ]
    )
    authorization = load_authorization(require_dynamic=False)
    authorization_active = authorization.get("authorization_gate_pass") is True
    denial_exact = (
        authorization.get("status") == DENIED_STATUS
        and authorization.get("denial_schema_valid") is True
        and authorization.get("dynamic_execution_allowed") is False
    )
    prerequisites = {
        "contract_identity_and_generated_files": generated["gate_pass"],
        "e0_failure_evidence_registration": e0["gate_pass"],
        "namespace_separation": all(namespace_gates.values()),
        "runtime_input_separation": all(runtime_input_gates.values()),
        "new_raw_root_absent": before["raw_root"]["kind"] == "absent",
        "new_lock_absent": before["lock_path"]["kind"] == "absent",
        "new_claim_absent": before["claim_path"]["kind"] == "absent",
        "new_ledger_absent": before["ledger_path"]["kind"] == "absent",
    }
    if not all(prerequisites.values()):
        after = side_effect_state()
        return {
            "schema_version": 1,
            "status": "HF3D_P0P2_PREFLIGHT_PREREQUISITE_FAILED_STOPPED",
            "gate_pass": False,
            "static_qualification": {
                "gate_pass": False,
                "gates": prerequisites,
            },
            "prerequisite_gates": prerequisites,
            "contract_id": contract.get("contract_id"),
            "authorization": authorization,
            "side_effect_state_before": before,
            "side_effect_state_after": after,
            "transaction_side_effects": {
                "side_effect_free": before == after,
                "claim_created": False,
                "ledger_created": False,
                "sample_directories_created": False,
            },
            "probe_results": [],
            "environment": None,
            "hf3d_time_steps_advanced": 0,
        }

    probe_results: list[dict[str, Any]] = []
    for benchmark in BENCHMARKS:
        result = probe_runner(benchmark)
        probe_results.append(result)
        if not result.get("gate_pass"):
            break
    probe_reports = [result.get("report", {}) for result in probe_results]
    process_identities = [
        (
            report.get("process_identity", {}).get("pid"),
            report.get("process_identity", {}).get("process_start_ticks"),
        )
        for report in probe_reports
        if isinstance(report, Mapping)
    ]
    all_probes = len(probe_results) == len(BENCHMARKS) and all(
        result.get("gate_pass") is True for result in probe_results
    )
    fresh_processes = (
        all_probes
        and len(process_identities) == len(BENCHMARKS)
        and len(set(process_identities)) == len(BENCHMARKS)
        and all(
            pid is not None and ticks is not None for pid, ticks in process_identities
        )
    )
    environment = (
        environment_runner(background_duration_s=background_duration_s)
        if all_probes
        else {
            "gate_pass": False,
            "stage": "not_run_due_to_zero_step_probe_failure",
        }
    )
    after = side_effect_state()
    side_effect_free = before == after
    component_gates = {
        **prerequisites,
        "all_three_exact_case_zero_step_probes": all_probes,
        "three_distinct_fresh_processes": fresh_processes,
        "environment": environment.get("gate_pass") is True,
        "authorization": authorization_active,
        "preflight_side_effect_free": side_effect_free,
    }
    static_gates = {
        key: value for key, value in component_gates.items() if key != "authorization"
    }
    static_gates["default_denial_exact_or_authorized_topology_valid"] = (
        denial_exact or authorization_active
    )
    gate_pass = all(component_gates.values())
    static_gate_pass = all(static_gates.values())
    if gate_pass:
        status = "HF3D_P0P2_PREFLIGHT_ALL_GATES_PASS_DYNAMIC_EXECUTION_ALLOWED"
    elif static_gate_pass and denial_exact:
        status = "HF3D_P0P2_ZERO_STEP_STATIC_PASS_DYNAMIC_EXECUTION_DENIED"
    else:
        status = "HF3D_P0P2_PREFLIGHT_FAILED_STOPPED"
    return {
        "schema_version": 1,
        "status": status,
        "gate_pass": gate_pass,
        "static_qualification": {
            "gate_pass": static_gate_pass,
            "gates": static_gates,
        },
        "component_gates": component_gates,
        "static_gates": static_gates,
        "contract_id": contract["contract_id"],
        "schedule_id": contract["fixed_schedule"]["schedule_id"],
        "canonical_schedule_sha256": contract["fixed_schedule"][
            "canonical_schedule_sha256"
        ],
        "authorization": authorization,
        "namespace_gates": namespace_gates,
        "runtime_input_gates": runtime_input_gates,
        "e0_registration": e0,
        "generated_outputs": generated,
        "probe_results": probe_results,
        "environment": environment,
        "side_effect_state_before": before,
        "side_effect_state_after": after,
        "transaction_side_effects": {
            "side_effect_free": side_effect_free,
            "claim_created": False,
            "ledger_created": False,
            "sample_directories_created": False,
        },
        "hf3d_time_steps_advanced": sum(
            int(report.get("hf3d_time_steps_advanced", 0))
            for report in probe_reports
            if isinstance(report, Mapping)
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zero-step-preflight", action="store_true", required=True)
    arguments = parser.parse_args()
    if not arguments.zero_step_preflight:
        return 2
    try:
        report = run_preflight()
    except Exception as exc:  # noqa: BLE001 - fail-closed preflight boundary
        report = {
            "schema_version": 1,
            "status": "HF3D_P0P2_PREFLIGHT_FAILED_STOPPED",
            "gate_pass": False,
            "static_qualification": {"gate_pass": False, "gates": {}},
            "exception_type": type(exc).__name__,
            "message": str(exc),
            "side_effect_state_after": side_effect_state(),
            "hf3d_time_steps_advanced": 0,
        }
    print(pretty_json_bytes(report).decode("utf-8"), end="")
    return 0 if report.get("gate_pass") is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
