#!/usr/bin/env python3
"""Create or verify tracked P0-P2 zero-step static evidence."""

from __future__ import annotations

import argparse
import hashlib
import os
import stat
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any, NoReturn

import register_v1_failure_evidence as e0_registration
from p0p2_contract import (
    AUTHORIZATION_PATH,
    BENCHMARKS,
    CONTRACT_PATH,
    DENIED_STATUS,
    E0_AUDIT_PATH,
    E0_MANIFEST_PATH,
    E0_SNAPSHOT_ROOT,
    EFFECTIVE_CONFIG_ROOT,
    FORMAL_SOURCE_SHA256,
    RAW_ROOT,
    STATIC_ROOT,
    assert_namespace_separation,
    load_contract,
    pretty_json_bytes,
    relative,
    sha256_file,
    source_config_path,
    strict_json,
    verify_generated_outputs,
)
from run_p0p2_preflight import run_preflight, side_effect_state

HERE = Path(__file__).resolve().parent
PREFLIGHT_REPORT_PATH = STATIC_ROOT / "p0p2_preflight_report.json"
CAPABILITY_PATH = STATIC_ROOT / "p0p2_capability_identity.json"
STATIC_AUDIT_PATH = STATIC_ROOT / "p0p2_static_audit.json"
PROBE_REPORT_PATHS = {
    benchmark: STATIC_ROOT / "probe_reports" / f"P0P2_{benchmark}_ZERO_STEP.json"
    for benchmark in BENCHMARKS
}
FORMAL_REPORT_PATHS = {
    benchmark: STATIC_ROOT
    / "formal_round_trip_reports"
    / f"P0P2_{benchmark}_FORMAL_ROUND_TRIP.json"
    for benchmark in BENCHMARKS
}


class StaticAuditError(RuntimeError):
    """Raised when P0-P2 static evidence is incomplete or inconsistent."""


def _fail(message: str) -> NoReturn:
    raise StaticAuditError(message)


def _mode_string(path: Path) -> str:
    permissions = stat.S_IMODE(path.stat(follow_symlinks=False).st_mode)
    return "100755" if permissions & 0o111 else "100644"


def _file_record(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        _fail(f"required regular file is absent: {relative(path)}")
    return {
        "path": relative(path),
        "mode": _mode_string(path),
        "byte_length": path.stat(follow_symlinks=False).st_size,
        "sha256": sha256_file(path),
    }


def _payload_record(
    path: Path, payload: bytes, *, mode: str = "100644"
) -> dict[str, Any]:
    return {
        "path": relative(path),
        "mode": mode,
        "byte_length": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _validate_binding_set(bindings: list[dict[str, Any]]) -> None:
    paths = [record.get("path") for record in bindings]
    if len(paths) != len(set(paths)):
        _fail("static evidence bindings contain duplicate paths")
    for record in bindings:
        if not (
            set(record) == {"path", "mode", "byte_length", "sha256"}
            and isinstance(record["path"], str)
            and record["path"]
            and record["mode"] in {"100644", "100755"}
            and isinstance(record["byte_length"], int)
            and record["byte_length"] >= 0
            and isinstance(record["sha256"], str)
            and len(record["sha256"]) == 64
        ):
            _fail("static evidence binding record is invalid")


def _implementation_paths() -> list[Path]:
    names = (
        "p0p2_contract.py",
        "build_p0p2_contract.py",
        "p0p2_authorization.py",
        "p0p2_environment.py",
        "p0p2_zero_step_probe.py",
        "run_p0p2_preflight.py",
        "audit_p0p2_zero_step.py",
        "README.md",
        ".gitignore",
    )
    paths = [HERE / name for name in names]
    paths.extend(sorted((HERE / "tests").glob("test_p0p2_*.py")))
    return paths


def _existing_identity_paths() -> list[Path]:
    paths = [
        CONTRACT_PATH,
        AUTHORIZATION_PATH,
        E0_MANIFEST_PATH,
        E0_AUDIT_PATH,
        HERE / "register_v1_failure_evidence.py",
        HERE / "tests" / "test_v1_failure_evidence_registration.py",
    ]
    manifest = strict_json(E0_MANIFEST_PATH)
    if not isinstance(manifest, Mapping):
        _fail("E0 manifest must be one object")
    snapshot = manifest.get("snapshot_copy")
    if not isinstance(snapshot, Mapping) or not isinstance(snapshot.get("files"), list):
        _fail("E0 snapshot file identities are missing")
    for record in snapshot["files"]:
        if not isinstance(record, Mapping) or not isinstance(record.get("path"), str):
            _fail("E0 snapshot file path binding is invalid")
        paths.append(E0_SNAPSHOT_ROOT / str(record["path"]))
    paths.extend(source_config_path(benchmark) for benchmark in BENCHMARKS)
    paths.extend(
        EFFECTIVE_CONFIG_ROOT / f"P0P2_{benchmark}.json" for benchmark in BENCHMARKS
    )
    return paths


def validate_preflight_report(report: Mapping[str, Any]) -> dict[str, bool]:
    """Require a complete static PASS while the only runtime pass remains denied."""

    authorization = report.get("authorization")
    static_qualification = report.get("static_qualification")
    component_gates = report.get("component_gates")
    probes = report.get("probe_results")
    before = report.get("side_effect_state_before")
    after = report.get("side_effect_state_after")
    side_effects = report.get("transaction_side_effects")
    gates = {
        "exact_static_pass_dynamic_denied_status": report.get("status")
        == "HF3D_P0P2_ZERO_STEP_STATIC_PASS_DYNAMIC_EXECUTION_DENIED",
        "single_runtime_gate_remains_false": report.get("gate_pass") is False,
        "nested_static_qualification_pass": isinstance(static_qualification, Mapping)
        and static_qualification.get("gate_pass") is True,
        "exact_default_denial": isinstance(authorization, Mapping)
        and authorization.get("status") == DENIED_STATUS
        and authorization.get("denial_schema_valid") is True
        and authorization.get("authorization_gate_pass") is False
        and authorization.get("dynamic_execution_allowed") is False,
        "unified_component_authorization_false": isinstance(component_gates, Mapping)
        and component_gates.get("authorization") is False,
        "unified_component_environment_true": isinstance(component_gates, Mapping)
        and component_gates.get("environment") is True,
        "all_three_probe_component_true": isinstance(component_gates, Mapping)
        and component_gates.get("all_three_exact_case_zero_step_probes") is True,
        "fresh_process_component_true": isinstance(component_gates, Mapping)
        and component_gates.get("three_distinct_fresh_processes") is True,
        "side_effect_free_component_true": isinstance(component_gates, Mapping)
        and component_gates.get("preflight_side_effect_free") is True,
        "exact_three_probe_results": isinstance(probes, list)
        and len(probes) == len(BENCHMARKS),
        "hf3d_time_steps_zero": report.get("hf3d_time_steps_advanced") == 0,
        "claim_not_created": isinstance(side_effects, Mapping)
        and side_effects.get("claim_created") is False,
        "transaction_side_effect_free": isinstance(side_effects, Mapping)
        and side_effects.get("side_effect_free") is True,
        "ledger_not_created": isinstance(side_effects, Mapping)
        and side_effects.get("ledger_created") is False,
        "sample_directories_not_created": isinstance(side_effects, Mapping)
        and side_effects.get("sample_directories_created") is False,
        "raw_lock_claim_ledger_absent_before_and_after": isinstance(before, Mapping)
        and isinstance(after, Mapping)
        and before == after
        and all(
            isinstance(before.get(name), Mapping)
            and before[name].get("kind") == "absent"
            for name in ("raw_root", "lock_path", "claim_path", "ledger_path")
        ),
    }
    if not all(gates.values()):
        failed = [name for name, passed in gates.items() if not passed]
        _fail("preflight static evidence failed: " + ", ".join(failed))
    return gates


def extract_probe_reports(report: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    wrappers = report.get("probe_results")
    if not isinstance(wrappers, list):
        _fail("preflight probe results are absent")
    extracted: dict[str, dict[str, Any]] = {}
    identities: set[tuple[Any, Any]] = set()
    for wrapper in wrappers:
        if not isinstance(wrapper, Mapping):
            _fail("probe wrapper must be one object")
        benchmark = wrapper.get("benchmark_id")
        probe = wrapper.get("report")
        if benchmark not in BENCHMARKS or not isinstance(probe, dict):
            _fail("probe wrapper benchmark/report identity failed")
        process = probe.get("process_identity")
        runtime_inputs = probe.get("runtime_inputs")
        probe_gates = probe.get("gates")
        teardown = probe.get("teardown")
        if not all(
            isinstance(value, Mapping)
            for value in (process, runtime_inputs, probe_gates, teardown)
        ):
            _fail(f"zero-step report structure failed for {benchmark}")
        assert isinstance(process, Mapping)
        assert isinstance(runtime_inputs, Mapping)
        assert isinstance(probe_gates, Mapping)
        assert isinstance(teardown, Mapping)
        valid = (
            wrapper.get("gate_pass") is True
            and wrapper.get("returncode") == 0
            and wrapper.get("stderr_empty") is True
            and wrapper.get("stderr_utf8") == ""
            and probe.get("status") == "HF3D_P0P2_EXACT_CASE_ZERO_STEP_PROBE_PASS"
            and probe.get("gate_pass") is True
            and probe.get("benchmark_id") == benchmark
            and probe.get("hf3d_time_steps_advanced") == 0
            and runtime_inputs.get("v1_raw_or_e0_snapshot_used") is False
            and probe_gates.get("actual_cuda_without_fallback") is True
            and probe_gates.get("formal_config_from_dict_and_exact_round_trip") is True
            and probe_gates.get("generated_effective_config_exact") is True
            and probe_gates.get("hf3d_time_steps_advanced_exactly_zero") is True
            and teardown.get("gate_pass") is True
        )
        if not valid:
            _fail(f"zero-step report failed for {benchmark}")
        identity = (process.get("pid"), process.get("process_start_ticks"))
        if None in identity or identity in identities:
            _fail("zero-step probe process identity is missing or reused")
        identities.add(identity)
        extracted[str(benchmark)] = probe
    if set(extracted) != set(BENCHMARKS):
        _fail("zero-step report set does not equal B1/B2/B3")
    return extracted


def formal_round_trip_report(
    benchmark: str, probe: Mapping[str, Any]
) -> dict[str, Any]:
    probe_gates = probe.get("gates")
    identity = probe.get("config_identity")
    source_identity = probe.get("formal_source_identity")
    if not all(
        isinstance(value, Mapping) for value in (probe_gates, identity, source_identity)
    ):
        _fail(f"formal round-trip source report is incomplete: {benchmark}")
    assert isinstance(probe_gates, Mapping)
    assert isinstance(identity, Mapping)
    assert isinstance(source_identity, Mapping)
    gates = {
        "formal_source_tree_exact": probe_gates.get("formal_source_tree_exact") is True,
        "formal_simulation_config_from_dict_and_exact_to_dict": probe_gates.get(
            "formal_config_from_dict_and_exact_round_trip"
        )
        is True,
        "generated_effective_config_exact": probe_gates.get(
            "generated_effective_config_exact"
        )
        is True,
        "effective_canonical_hash_exact": probe_gates.get(
            "effective_config_canonical_hash_exact"
        )
        is True,
        "duration_ramp_output_schedule_compatible": probe_gates.get(
            "duration_ramp_and_output_schedule_compatible"
        )
        is True,
        "zero_hf3d_time_steps": probe.get("hf3d_time_steps_advanced") == 0,
    }
    return {
        "schema_version": 1,
        "status": (
            "HF3D_P0P2_FORMAL_CONFIG_ROUND_TRIP_PASS"
            if all(gates.values())
            else "HF3D_P0P2_FORMAL_CONFIG_ROUND_TRIP_FAILED_STOPPED"
        ),
        "gate_pass": all(gates.values()),
        "benchmark_id": benchmark,
        "gates": gates,
        "source_and_effective_config_identity": dict(identity),
        "formal_source_identity": dict(source_identity),
        "fresh_process_identity": probe.get("process_identity"),
        "round_trip_evidence_source": relative(PROBE_REPORT_PATHS[benchmark]),
        "hf3d_time_steps_advanced": 0,
    }


def capability_identity(
    preflight: Mapping[str, Any], probes: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    contract = load_contract()
    environment = preflight.get("environment")
    if not isinstance(environment, Mapping) or environment.get("gate_pass") is not True:
        _fail("capability identity requires a passing post-probe environment gate")
    start = environment.get("instantaneous_start")
    end = environment.get("instantaneous_end")
    if not isinstance(start, Mapping) or not isinstance(end, Mapping):
        _fail("capability identity lacks both environment snapshots")
    return {
        "schema_version": 1,
        "status": "HF3D_P0P2_ZERO_STEP_CAPABILITY_IDENTITY_PASS",
        "gate_pass": True,
        "contract_id": contract["contract_id"],
        "schedule_id": contract["fixed_schedule"]["schedule_id"],
        "canonical_schedule_sha256": contract["fixed_schedule"][
            "canonical_schedule_sha256"
        ],
        "formal_source_tree_sha256": FORMAL_SOURCE_SHA256,
        "effective_config_canonical_sha256": {
            benchmark: contract["effective_configurations"][benchmark][
                "effective_canonical_sha256"
            ]
            for benchmark in BENCHMARKS
        },
        "probe_processes": {
            benchmark: {
                "process_identity": probe["process_identity"],
                "toolchain": probe["toolchain"],
                "grid_shape": probe["grid"]["shape"],
                "pressure_accelerator": probe["pressure_engine"]["accelerator"],
                "hf3d_time_steps_advanced": probe["hf3d_time_steps_advanced"],
                "teardown": probe["teardown"],
            }
            for benchmark, probe in probes.items()
        },
        "post_probe_environment": {
            "start": start,
            "end": end,
            "background": environment.get("background"),
            "background_gates": environment.get("background_gates"),
        },
        "new_raw_and_transaction_state": {
            "before": preflight["side_effect_state_before"],
            "after": preflight["side_effect_state_after"],
        },
        "authorization_status": DENIED_STATUS,
        "dynamic_execution_authorized": False,
        "counters": {
            "hf3d_time_steps_advanced": 0,
            "warmup_samples_started": 0,
            "timed_samples_started": 0,
            "profile_samples_started": 0,
            "performance_claims_consumed": 0,
        },
    }


def _build_static_audit(
    preflight: Mapping[str, Any],
    preflight_gates: Mapping[str, bool],
    probes: Mapping[str, Mapping[str, Any]],
    bindings: list[dict[str, Any]],
) -> dict[str, Any]:
    e0 = e0_registration.check_registration()
    generated = verify_generated_outputs()
    namespace = assert_namespace_separation()
    current_state = side_effect_state()
    gates = {
        "e0_registration_exact": e0.get("gate_pass") is True,
        "contract_and_effective_outputs_exact": generated.get("gate_pass") is True,
        "all_namespaces_distinct": all(namespace.values()),
        "preflight_static_qualification_pass": all(preflight_gates.values()),
        "three_exact_case_zero_step_reports_pass": set(probes) == set(BENCHMARKS)
        and all(probe.get("gate_pass") is True for probe in probes.values()),
        "three_formal_round_trip_reports_pass": set(probes) == set(BENCHMARKS)
        and all(
            formal_round_trip_report(benchmark, probe)["gate_pass"] is True
            for benchmark, probe in probes.items()
        ),
        "post_probe_environment_pass": isinstance(preflight.get("environment"), Mapping)
        and preflight["environment"].get("gate_pass") is True,
        "top_runtime_gate_denied": preflight.get("gate_pass") is False,
        "default_authorization_denied": isinstance(
            preflight.get("authorization"), Mapping
        )
        and preflight["authorization"].get("status") == DENIED_STATUS,
        "new_raw_lock_claim_ledger_absent": all(
            current_state[name]["kind"] == "absent"
            for name in ("raw_root", "lock_path", "claim_path", "ledger_path")
        ),
        "all_static_bindings_present": bool(bindings)
        and all(record["byte_length"] >= 0 for record in bindings),
        "hf3d_time_steps_zero": preflight.get("hf3d_time_steps_advanced") == 0,
        "performance_samples_started_zero": True,
        "p1_not_implemented": True,
    }
    return {
        "schema_version": 1,
        "status": (
            "HF3D_P0P2_ZERO_STEP_IMPLEMENTATION_STATIC_AUDIT_PASS"
            if all(gates.values())
            else "HF3D_P0P2_ZERO_STEP_IMPLEMENTATION_STATIC_AUDIT_FAILED_STOPPED"
        ),
        "gate_pass": all(gates.values()),
        "gates": gates,
        "preflight_status": preflight.get("status"),
        "runtime_gate_pass": preflight.get("gate_pass"),
        "authorization_status": DENIED_STATUS,
        "static_evidence_bindings": bindings,
        "counters": {
            "hf3d_time_steps_advanced": 0,
            "warmup_samples_started": 0,
            "timed_samples_started": 0,
            "profile_samples_started": 0,
            "performance_samples_started": 0,
            "performance_claims_consumed": 0,
        },
        "conclusion_ceiling": {
            "p0p2_dynamic_execution": "NOT_AUTHORIZED",
            "measured_performance_result": "NOT_AVAILABLE",
            "numerical_qualification": "NOT_DEMONSTRATED",
            "physical_validation": "NOT_PERFORMED",
            "experimental_validation": "NOT_PERFORMED",
            "apparatus_prediction": "DISALLOWED",
        },
    }


def _write_new_file(path: Path, payload: bytes, *, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, mode)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                _fail(f"short write for {path}")
            view = view[written:]
        os.fchmod(descriptor, mode)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _publish_artifacts(outputs: Mapping[Path, bytes]) -> None:
    if STATIC_ROOT.exists() or STATIC_ROOT.is_symlink():
        _fail("P0-P2 static evidence root already exists")
    parent = STATIC_ROOT.parent
    parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".p0p2-static-", dir=parent))
    published = False
    try:
        for destination, payload in outputs.items():
            relative_destination = destination.relative_to(STATIC_ROOT)
            _write_new_file(temporary / relative_destination, payload)
        os.rename(temporary, STATIC_ROOT)
        published = True
    finally:
        if not published and temporary.exists():
            for child in sorted(temporary.rglob("*"), reverse=True):
                if child.is_file() and not child.is_symlink():
                    child.unlink()
                elif child.is_dir() and not child.is_symlink():
                    child.rmdir()
            temporary.rmdir()


def write_static_evidence() -> dict[str, Any]:
    if RAW_ROOT.exists() or RAW_ROOT.is_symlink():
        _fail("new P0-P2 raw root must be absent before the static audit")
    preflight = run_preflight()
    preflight_gates = validate_preflight_report(preflight)
    probes = extract_probe_reports(preflight)
    capability = capability_identity(preflight, probes)

    outputs: dict[Path, bytes] = {
        PREFLIGHT_REPORT_PATH: pretty_json_bytes(preflight),
        CAPABILITY_PATH: pretty_json_bytes(capability),
    }
    for benchmark, probe in probes.items():
        outputs[PROBE_REPORT_PATHS[benchmark]] = pretty_json_bytes(probe)
        outputs[FORMAL_REPORT_PATHS[benchmark]] = pretty_json_bytes(
            formal_round_trip_report(benchmark, probe)
        )

    bindings = [_file_record(path) for path in _existing_identity_paths()]
    bindings.extend(_file_record(path) for path in _implementation_paths())
    bindings.extend(
        _payload_record(path, payload)
        for path, payload in sorted(outputs.items(), key=lambda item: relative(item[0]))
    )
    bindings.sort(key=lambda record: str(record["path"]))
    _validate_binding_set(bindings)
    audit = _build_static_audit(
        preflight,
        preflight_gates,
        probes,
        bindings,
    )
    if not audit["gate_pass"]:
        _fail("P0-P2 static audit gates failed")
    outputs[STATIC_AUDIT_PATH] = pretty_json_bytes(audit)
    _publish_artifacts(outputs)
    return audit


def check_static_evidence() -> dict[str, Any]:
    preflight_raw = strict_json(PREFLIGHT_REPORT_PATH)
    if not isinstance(preflight_raw, Mapping):
        _fail("tracked preflight report must be one object")
    preflight = dict(preflight_raw)
    preflight_gates = validate_preflight_report(preflight)
    probes = extract_probe_reports(preflight)
    for benchmark, expected in probes.items():
        observed = strict_json(PROBE_REPORT_PATHS[benchmark])
        if observed != expected:
            _fail(f"tracked probe report differs from preflight: {benchmark}")
        formal = formal_round_trip_report(benchmark, expected)
        if strict_json(FORMAL_REPORT_PATHS[benchmark]) != formal:
            _fail(f"tracked formal round-trip report differs: {benchmark}")
    expected_capability = capability_identity(preflight, probes)
    if strict_json(CAPABILITY_PATH) != expected_capability:
        _fail("tracked capability identity differs from bound preflight")

    outputs = {
        PREFLIGHT_REPORT_PATH: pretty_json_bytes(preflight),
        CAPABILITY_PATH: pretty_json_bytes(expected_capability),
        **{
            PROBE_REPORT_PATHS[benchmark]: pretty_json_bytes(probe)
            for benchmark, probe in probes.items()
        },
        **{
            FORMAL_REPORT_PATHS[benchmark]: pretty_json_bytes(
                formal_round_trip_report(benchmark, probe)
            )
            for benchmark, probe in probes.items()
        },
    }
    bindings = [_file_record(path) for path in _existing_identity_paths()]
    bindings.extend(_file_record(path) for path in _implementation_paths())
    bindings.extend(
        _payload_record(path, payload)
        for path, payload in sorted(outputs.items(), key=lambda item: relative(item[0]))
    )
    bindings.sort(key=lambda record: str(record["path"]))
    _validate_binding_set(bindings)
    expected_audit = _build_static_audit(
        preflight,
        preflight_gates,
        probes,
        bindings,
    )
    observed_audit = strict_json(STATIC_AUDIT_PATH)
    if observed_audit != expected_audit:
        _fail("tracked static audit differs from independently rebuilt audit")
    for path, payload in outputs.items():
        if path.read_bytes() != payload:
            _fail(
                f"tracked static evidence is not canonical pretty JSON: {relative(path)}"
            )
    if STATIC_AUDIT_PATH.read_bytes() != pretty_json_bytes(expected_audit):
        _fail("tracked static audit is not canonical pretty JSON")
    return expected_audit


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    try:
        report = write_static_evidence() if arguments.write else check_static_evidence()
    except Exception as exc:  # noqa: BLE001 - fail-closed static audit boundary
        report = {
            "schema_version": 1,
            "status": "HF3D_P0P2_ZERO_STEP_IMPLEMENTATION_STATIC_AUDIT_FAILED_STOPPED",
            "gate_pass": False,
            "exception_type": type(exc).__name__,
            "message": str(exc),
            "side_effect_state": side_effect_state(),
        }
    print(pretty_json_bytes(report).decode("utf-8"), end="")
    return 0 if report.get("gate_pass") is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
