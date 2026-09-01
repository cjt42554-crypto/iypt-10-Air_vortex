"""Pure identities and fail-closed helpers for the independent P0-P2 protocol."""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any, NoReturn

HERE = Path(__file__).resolve().parent
REPOSITORY_ROOT = HERE.parents[2]
FORMAL_ROOT = REPOSITORY_ROOT / "02_模拟程序"
FORMAL_SOURCE = FORMAL_ROOT / "src"
D3_ROOT = HERE.parent / "07_D3跨网格时间步响应诊断"
SOURCE_CONFIG_ROOT = D3_ROOT / "generated_configs"

CONTRACT_PATH = HERE / "p0p2_contract.json"
AUTHORIZATION_PATH = HERE / "p0p2_execution_authorization.json"
TICKET_PATH = HERE / "p0p2_authorization_ticket.json"
EFFECTIVE_CONFIG_ROOT = HERE / "generated_effective_configs"
STATIC_ROOT = HERE / "artifacts" / "p0p2" / "static"
RAW_ROOT = HERE / "artifacts" / "p0p2" / "performance_raw"
LOCK_PATH = HERE / "artifacts" / "p0p2" / ".p0p2_campaign.lock"

E0_MANIFEST_PATH = HERE / "evidence" / "v1_failure_evidence_manifest.json"
E0_AUDIT_PATH = HERE / "evidence" / "v1_failure_evidence_audit.json"
E0_SNAPSHOT_ROOT = HERE / "evidence" / "v1_claim_failure_snapshot"
V1_RAW_ROOT = HERE.parent / "17_HF3D性能优化" / "artifacts" / "p0" / "performance_raw"
PRO_RULING_PATH = HERE.parent / "23_P0P一次性执行失败独立复核与P0P2协议裁决.md"

GPU_PYTHON = HERE.parent / "01_HF3D数值资格" / ".venv-gpu" / "bin" / "python"

CONTRACT_ID = "HF3D_P0P2_ZERO_STEP_AND_FUTURE_PERFORMANCE_BASELINE_V1"
SCHEDULE_ID = "HF3D_P0P2_FIXED_18_V1"
AUTHORIZATION_ID = "HF3D_P0P2_DYNAMIC_EXECUTION_AUTHORIZATION_V1"
CLAIM_NAMESPACE = "HF3D_P0P2_SINGLE_SCHEDULE_CLAIM_V1"
LEDGER_SCHEMA_ID = "HF3D_P0P2_FIXED_18_LEDGER_V1"
GRANT_NONCE_PREFIX = "HF3D_P0P2_GRANT_NONCE_"

DENIED_STATUS = "NOT_AUTHORIZED_PENDING_PRO_STATIC_REVIEW"
AUTHORIZED_STATUS = "AUTHORIZED_BY_PRO_FOR_BOUND_P0P2_PARENT_TICKET"

E0_COMMIT = "379ae8dfb425c2f57e25d2779525fc843592fc98"
E0_TREE = "0bf995de5bbd0512a2b2b2aaaf9c2252e41dafe0"
PRO_RULING_COMMIT = "0350fc6653988af6dded2bbc2c0cfce79de7631a"
FORMAL_SOURCE_SHA256 = (
    "dcd27cbf4353ab8b0d53802c4810f4423d3a34a45ea2944bdd8346e4eab54bb8"
)
E0_CANONICAL_TREE_SHA256 = (
    "21ef41a6acc791c01da67b751cbaeaabfdaa94ee1d3a860948f0700b7dd4e541"
)
E0_MANIFEST_SHA256 = "0c2e84e117970cc34ad9ae0793bb5f6edf17090d1da3d064e42ed0abfd3a3e6a"
E0_AUDIT_SHA256 = "2ffb6cc8e2286585604e3be5885f403ef6783fa13b00f1c924251b5b9a1a5860"
PRO_RULING_SHA256 = "57a79ede373f945a8d7e1ff408b5aa18a6d61b45a0c030dac7e2b70bf4690969"
E0_MANIFEST_BLOB = "7f181df1af857cae9f040c4f5593ab65c777d206"
E0_AUDIT_BLOB = "27035b70748d7fe550a437054719533279212d9c"
PRO_RULING_BLOB = "ec166811d0e4cce8bda997d7f58a1196d483c658"

DURATION_S = 0.005
OUTPUT_INTERVAL_S = 0.001
REGISTERED_TIMES_S = (0.0, 0.001, 0.002, 0.003, 0.004, 0.005)

BENCHMARKS: dict[str, dict[str, Any]] = {
    "B1": {
        "source_case": "G6_DT0",
        "source_config_sha256": (
            "602dead5fa6b8b4485103bf91adb53b1bb17b6b3e3f9c8f7c0578be4242d0cd0"
        ),
        "effective_config_sha256": (
            "e694ea2b4c3b1a686e066cb7f6a919367e7bd1a340f0738964ce4e157389f00b"
        ),
    },
    "B2": {
        "source_case": "G9_DT0",
        "source_config_sha256": (
            "64bf8553b88e546b3bd18a8867fd9ed5455be475bb77f467f8c45c157353b86d"
        ),
        "effective_config_sha256": (
            "d51aed9bb2e90af300d980b00324214cf991aa483c15f4a46a8b2b669d8731c4"
        ),
    },
    "B3": {
        "source_case": "G9_DT2",
        "source_config_sha256": (
            "c924a225d4d35b25217318c0bdbe7728bceb637f5474e0eb9e2cbffac6ccdb37"
        ),
        "effective_config_sha256": (
            "2c3ac702a5e85762eecf2f15bb908f538c697058604d1e5edddad051564618e3"
        ),
    },
}

_LOGICAL_SCHEDULE = (
    ("WARMUP", "B1", "NONE", False),
    ("WARMUP", "B2", "NONE", False),
    ("WARMUP", "B3", "NONE", False),
    ("TIMED_R1", "B1", "NONE", True),
    ("TIMED_R1", "B2", "NONE", True),
    ("TIMED_R1", "B3", "NONE", True),
    ("TIMED_R2", "B2", "NONE", True),
    ("TIMED_R2", "B3", "NONE", True),
    ("TIMED_R2", "B1", "NONE", True),
    ("TIMED_R3", "B3", "NONE", True),
    ("TIMED_R3", "B1", "NONE", True),
    ("TIMED_R3", "B2", "NONE", True),
    ("PROFILE_HOST", "B2", "CPROFILE_SEMANTIC_WRAPPERS_PIDSTAT_NVIDIA_SMI", False),
    ("PROFILE_CUDA", "B2", "NSYS_CUDA_NVTX_PIDSTAT_NVIDIA_SMI", False),
    ("PROFILE_MEMORY", "B2", "NATIVE_AND_CUPY_ALLOCATORS_RSS_NVIDIA_SMI", False),
    ("PROFILE_HOST", "B3", "CPROFILE_SEMANTIC_WRAPPERS_PIDSTAT_NVIDIA_SMI", False),
    ("PROFILE_CUDA", "B3", "NSYS_CUDA_NVTX_PIDSTAT_NVIDIA_SMI", False),
    ("PROFILE_MEMORY", "B3", "NATIVE_AND_CUPY_ALLOCATORS_RSS_NVIDIA_SMI", False),
)


class P0P2Error(RuntimeError):
    """Raised when an independent P0-P2 identity or gate is invalid."""


def _fail(message: str) -> NoReturn:
    raise P0P2Error(message)


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> NoReturn:
    _fail(f"non-finite JSON number is prohibited: {value}")


def strict_json_bytes(data: bytes, *, label: str) -> Any:
    try:
        return json.loads(
            data.decode("utf-8", errors="strict"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise P0P2Error(f"invalid strict JSON at {label}: {exc}") from exc


def strict_json(path: Path) -> Any:
    if not path.is_file() or path.is_symlink():
        _fail(f"required regular JSON file is absent: {relative(path)}")
    return strict_json_bytes(path.read_bytes(), label=relative(path))


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def pretty_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPOSITORY_ROOT.resolve()).as_posix()
    except ValueError:
        return path.absolute().as_posix()


def _git_bytes(*arguments: str) -> bytes:
    completed = subprocess.run(
        ["git", "-c", "core.quotePath=false", *arguments],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        _fail(f"Git identity command failed: {detail}")
    return completed.stdout


def _git_text(*arguments: str) -> str:
    return _git_bytes(*arguments).decode("utf-8", errors="strict").strip()


def source_config_path(benchmark: str) -> Path:
    definition = BENCHMARKS.get(benchmark)
    if definition is None:
        _fail(f"unknown P0-P2 benchmark: {benchmark}")
    return SOURCE_CONFIG_ROOT / f"{definition['source_case']}.json"


def _leaf_differences(
    baseline: object, effective: object, prefix: str = ""
) -> dict[str, dict[str, Any]]:
    differences: dict[str, dict[str, Any]] = {}
    if isinstance(baseline, Mapping) and isinstance(effective, Mapping):
        for key in sorted(set(baseline) | set(effective)):
            dotted = f"{prefix}.{key}" if prefix else str(key)
            if key not in baseline or key not in effective:
                differences[dotted] = {
                    "baseline": baseline.get(key),
                    "effective": effective.get(key),
                }
            else:
                differences.update(
                    _leaf_differences(baseline[key], effective[key], dotted)
                )
    elif baseline != effective:
        differences[prefix] = {"baseline": baseline, "effective": effective}
    return differences


def validate_effective_payload(
    benchmark: str, payload: Mapping[str, Any]
) -> dict[str, Any]:
    """Bind one candidate payload to its source and the only allowed leaf change."""

    definition = BENCHMARKS.get(benchmark)
    if definition is None:
        _fail(f"unknown P0-P2 benchmark: {benchmark}")
    source_path = source_config_path(benchmark)
    actual_source_hash = sha256_file(source_path)
    if actual_source_hash != definition["source_config_sha256"]:
        _fail(f"{benchmark} source configuration hash drifted")
    baseline = strict_json(source_path)
    if not isinstance(baseline, dict):
        _fail(f"{benchmark} source configuration must be one object")
    candidate = deepcopy(dict(payload))
    source_run = baseline.get("run")
    if not isinstance(source_run, dict) or "duration_s" not in source_run:
        _fail(f"{benchmark} source run.duration_s is missing")
    differences = _leaf_differences(baseline, candidate)
    expected = {
        "run.duration_s": {
            "baseline": source_run["duration_s"],
            "effective": DURATION_S,
        }
    }
    if differences != expected:
        _fail(f"{benchmark} effective configuration changed extra fields")
    actual_effective_hash = canonical_sha256(candidate)
    if actual_effective_hash != definition["effective_config_sha256"]:
        _fail(f"{benchmark} effective configuration canonical hash drifted")
    run_effective = candidate.get("run", {})
    drive = candidate.get("drive", {})
    if not (
        isinstance(run_effective, Mapping)
        and isinstance(drive, Mapping)
        and run_effective.get("duration_s") == DURATION_S
        and run_effective.get("output_interval_s") == OUTPUT_INTERVAL_S
        and drive.get("ramp_duration_s") == DURATION_S
        and drive.get("actual_speed_schedule_time_s") == []
        and len(REGISTERED_TIMES_S) == 6
        and REGISTERED_TIMES_S[-1] == DURATION_S
        and all(
            math.isclose(
                value,
                index * OUTPUT_INTERVAL_S,
                rel_tol=0.0,
                abs_tol=0.0,
            )
            for index, value in enumerate(REGISTERED_TIMES_S)
        )
    ):
        _fail(f"{benchmark} duration/ramp/output compatibility failed")
    return {
        "benchmark_id": benchmark,
        "source_case": definition["source_case"],
        "source_path": relative(source_path),
        "source_file_sha256": actual_source_hash,
        "source_canonical_sha256": canonical_sha256(baseline),
        "effective_config_path": relative(
            EFFECTIVE_CONFIG_ROOT / f"P0P2_{benchmark}.json"
        ),
        "effective_canonical_sha256": actual_effective_hash,
        "only_difference": differences,
        "formal_round_trip_required": True,
    }


def effective_config(benchmark: str) -> tuple[dict[str, Any], dict[str, Any]]:
    definition = BENCHMARKS.get(benchmark)
    if definition is None:
        _fail(f"unknown P0-P2 benchmark: {benchmark}")
    source_path = source_config_path(benchmark)
    if sha256_file(source_path) != definition["source_config_sha256"]:
        _fail(f"{benchmark} source configuration hash drifted")
    baseline = strict_json(source_path)
    if not isinstance(baseline, dict):
        _fail(f"{benchmark} source configuration must be one object")
    effective = deepcopy(baseline)
    run = effective.get("run")
    if not isinstance(run, dict) or "duration_s" not in run:
        _fail(f"{benchmark} source run.duration_s is missing")
    source_duration = run["duration_s"]
    run["duration_s"] = DURATION_S
    if source_duration == DURATION_S:
        _fail(f"{benchmark} source duration unexpectedly equals P0-P2 duration")
    return effective, validate_effective_payload(benchmark, effective)


def fixed_schedule() -> dict[str, Any]:
    ordered: list[dict[str, Any]] = []
    for sequence_index, (mode, benchmark, profiler, scored) in enumerate(
        _LOGICAL_SCHEDULE
    ):
        sample_id = f"P0P2_{mode}_{benchmark}"
        ordered.append(
            {
                "sequence_index": sequence_index,
                "sample_id": sample_id,
                "benchmark_id": benchmark,
                "mode": mode,
                "scored": scored,
                "profiler_policy": profiler,
                "duration_s": DURATION_S,
                "registered_output_times_s": list(REGISTERED_TIMES_S),
                "only_allowed_source_config_change": "run.duration_s",
                "source_config_sha256": BENCHMARKS[benchmark]["source_config_sha256"],
                "effective_config_sha256": BENCHMARKS[benchmark][
                    "effective_config_sha256"
                ],
            }
        )
    schedule_without_hash = {
        "schema_version": 1,
        "schedule_id": SCHEDULE_ID,
        "sample_namespace": "P0P2_",
        "sample_reorder": "PROHIBITED",
        "sample_replacement": "PROHIBITED",
        "schedule_resume_after_claim": "PROHIBITED",
        "schedule_retry_or_supplement": "PROHIBITED",
        "ordered_items": ordered,
    }
    return {
        **schedule_without_hash,
        "canonical_schedule_sha256": canonical_sha256(schedule_without_hash),
    }


def _bound_git_file(
    *, commit: str, path: Path, expected_blob: str, expected_sha256: str
) -> dict[str, Any]:
    relative_path = relative(path)
    worktree_bytes = path.read_bytes()
    commit_bytes = _git_bytes("cat-file", "blob", f"{commit}:{relative_path}")
    if worktree_bytes != commit_bytes:
        _fail(f"bound worktree file differs from {commit}: {relative_path}")
    actual_blob = _git_text("rev-parse", f"{commit}:{relative_path}")
    actual_sha256 = hashlib.sha256(worktree_bytes).hexdigest()
    if actual_blob != expected_blob or actual_sha256 != expected_sha256:
        _fail(f"bound Git file identity drifted: {relative_path}")
    return {
        "path": relative_path,
        "commit": commit,
        "git_blob": actual_blob,
        "sha256": actual_sha256,
        "byte_length": len(worktree_bytes),
        "mode": "100644",
    }


def build_contract() -> dict[str, Any]:
    if _git_text("rev-parse", f"{E0_COMMIT}^{{tree}}") != E0_TREE:
        _fail("E0 commit tree identity drifted")
    e0_manifest = strict_json(E0_MANIFEST_PATH)
    e0_audit = strict_json(E0_AUDIT_PATH)
    if not (
        e0_manifest.get("status") == "HF3D_P0P_V1_FAILURE_EVIDENCE_REGISTERED_IMMUTABLY"
        and e0_manifest.get("source_after", {}).get("canonical_tree_sha256")
        == E0_CANONICAL_TREE_SHA256
        and e0_manifest.get("snapshot_copy", {}).get("canonical_tree_sha256")
        == E0_CANONICAL_TREE_SHA256
        and e0_audit.get("status") == "HF3D_P0P_V1_E0_REGISTRATION_PASS"
        and e0_audit.get("gate_pass") is True
    ):
        _fail("E0 evidence registration is not the accepted immutable identity")
    schedule = fixed_schedule()
    effective_bindings: dict[str, Any] = {}
    for benchmark in BENCHMARKS:
        _payload, identity = effective_config(benchmark)
        effective_bindings[benchmark] = identity

    manifest_binding = _bound_git_file(
        commit=E0_COMMIT,
        path=E0_MANIFEST_PATH,
        expected_blob=E0_MANIFEST_BLOB,
        expected_sha256=E0_MANIFEST_SHA256,
    )
    audit_binding = _bound_git_file(
        commit=E0_COMMIT,
        path=E0_AUDIT_PATH,
        expected_blob=E0_AUDIT_BLOB,
        expected_sha256=E0_AUDIT_SHA256,
    )
    ruling_binding = _bound_git_file(
        commit=PRO_RULING_COMMIT,
        path=PRO_RULING_PATH,
        expected_blob=PRO_RULING_BLOB,
        expected_sha256=PRO_RULING_SHA256,
    )

    return {
        "schema_version": 1,
        "contract_id": CONTRACT_ID,
        "status": "HF3D_P0P2_ZERO_STEP_IMPLEMENTATION_CONTRACT_FROZEN",
        "purpose": "ENGINEERING_PERFORMANCE_ATTRIBUTION_BASELINE_ONLY",
        "reviewed_parent": {
            "e0_commit": E0_COMMIT,
            "e0_tree": E0_TREE,
            "pro_ruling": ruling_binding,
        },
        "formal_source_tree_sha256": FORMAL_SOURCE_SHA256,
        "e0_failure_evidence": {
            "manifest": manifest_binding,
            "audit": audit_binding,
            "canonical_tree_sha256": E0_CANONICAL_TREE_SHA256,
            "role": "FAILURE_EVIDENCE_AND_NEGATIVE_REGRESSION_ONLY",
            "runtime_input": "PROHIBITED",
        },
        "effective_configurations": effective_bindings,
        "fixed_schedule": schedule,
        "frozen_short_window": {
            "run_duration_s": DURATION_S,
            "drive_ramp_duration_s": DURATION_S,
            "drive_ramp_modified": False,
            "output_interval_s": OUTPUT_INTERVAL_S,
            "registered_output_times_s": list(REGISTERED_TIMES_S),
            "only_allowed_source_config_change": "run.duration_s",
        },
        "identity_namespaces": {
            "authorization_id": AUTHORIZATION_ID,
            "claim_namespace": CLAIM_NAMESPACE,
            "ledger_schema_id": LEDGER_SCHEMA_ID,
            "sample_prefix": "P0P2_",
            "ticket_prefix": "HF3D_P0P2_PRO_TICKET_",
            "grant_nonce_namespace": GRANT_NONCE_PREFIX,
        },
        "paths": {
            "contract": relative(CONTRACT_PATH),
            "authorization": relative(AUTHORIZATION_PATH),
            "future_ticket": relative(TICKET_PATH),
            "new_raw_root": relative(RAW_ROOT),
            "new_lock": relative(LOCK_PATH),
            "new_claim": relative(RAW_ROOT / "execution_claim.json"),
            "new_ledger": relative(RAW_ROOT / "schedule_ledger.json"),
            "static_root": relative(STATIC_ROOT),
            "v1_raw_root": relative(V1_RAW_ROOT),
            "e0_snapshot_root": relative(E0_SNAPSHOT_ROOT),
        },
        "preclaim_gates": {
            "source_hash_and_only_run_duration_changed": True,
            "simulation_config_from_dict": True,
            "exact_to_dict_round_trip": True,
            "effective_canonical_hash_binding": True,
            "drive_and_output_schedule_compatibility": True,
            "fresh_process_per_benchmark": True,
            "actual_cuda_without_fallback": True,
            "grid_pressure_initial_state_zero_step_and_initial_frame": True,
            "hf3d_time_steps_advanced_exact": 0,
            "e0_evidence_identity": True,
            "new_raw_root_absent": True,
            "environment_in_single_top_level_conjunction": True,
            "authorization_in_single_top_level_conjunction": True,
            "failure_before_lock_raw_claim_ledger_or_sample": True,
        },
        "environment_policy": {
            "online_logical_cpus": 32,
            "cpu_tctl_max_c": 90.0,
            "gpu_temperature_max_c": 75.0,
            "background_observation_s": 30.0,
            "background_cpu_percent_max_one_logical_cpu_basis": 25.0,
            "no_other_compute_process": True,
            "no_other_gpu_compute_process": True,
            "ac_power_online": True,
        },
        "toolchain_policy": {
            "gpu_python": relative(GPU_PYTHON),
            "python_version": "3.12.3",
            "numpy_version": "2.5.2",
            "scipy_version": "1.17.1",
            "cupy_distribution": "cupy-cuda13x",
            "cupy_version": "14.2.0",
            "gpu_name": "NVIDIA GeForce RTX 5060 Laptop GPU",
            "gpu_uuid": "GPU-dc6a96b5-fcac-d5a9-f9a6-720d53fb3f0e",
        },
        "runtime_input_policy": {
            "allowed": [
                "three bound D3R1 source configuration files",
                "formal 02_模拟程序 source tree",
                "this P0-P2 contract and future exact authorization objects",
            ],
            "prohibited_prefixes": [
                relative(V1_RAW_ROOT),
                relative(E0_SNAPSHOT_ROOT),
            ],
            "v1_claim_ledger_sample_prefix_or_state_reuse": "PROHIBITED",
        },
        "future_transaction": {
            "maximum_schedule_claims": 1,
            "claim_before_first_sample": True,
            "preflight_consumes_claim": False,
            "sample_replacement": "PROHIBITED",
            "schedule_resume_after_claim": "PROHIBITED",
            "schedule_retry_or_supplement": "PROHIBITED",
            "P1": "PROHIBITED",
        },
        "current_authority": {
            "zero_step_implementation": True,
            "parent_ticket_issuance": False,
            "dynamic_samples": False,
            "P1": False,
        },
        "conclusion_ceiling": {
            "p0p_v1_measured_performance_result": "NOT_AVAILABLE",
            "p0p2_dynamic_execution": "NOT_AUTHORIZED",
            "numerical_qualification": "NOT_DEMONSTRATED",
            "physical_validation": "NOT_PERFORMED",
            "experimental_validation": "NOT_PERFORMED",
            "apparatus_prediction": "DISALLOWED",
        },
    }


def default_authorization(contract: Mapping[str, Any] | None = None) -> dict[str, Any]:
    value = build_contract() if contract is None else dict(contract)
    schedule = value["fixed_schedule"]
    return {
        "schema_version": 2,
        "status": DENIED_STATUS,
        "authorization_id": AUTHORIZATION_ID,
        "contract_id": CONTRACT_ID,
        "dynamic_samples_authorized": False,
        "reviewed_snapshot_commit": None,
        "ticket_id": None,
        "ticket_path": relative(TICKET_PATH),
        "fixed_schedule_id": SCHEDULE_ID,
        "fixed_schedule_sha256": schedule["canonical_schedule_sha256"],
        "grant_nonce": None,
        "maximum_schedule_claims": 0,
        "preflight_consumes_claim": False,
        "claim_before_first_sample": True,
        "sample_replacement": "PROHIBITED",
        "schedule_resume_after_claim": "PROHIBITED",
        "schedule_retry_or_supplement": "PROHIBITED",
        "P1": "PROHIBITED",
    }


def generated_outputs() -> dict[Path, bytes]:
    contract = build_contract()
    outputs: dict[Path, bytes] = {
        CONTRACT_PATH: pretty_json_bytes(contract),
        AUTHORIZATION_PATH: pretty_json_bytes(default_authorization(contract)),
    }
    for benchmark in BENCHMARKS:
        payload, _identity = effective_config(benchmark)
        outputs[EFFECTIVE_CONFIG_ROOT / f"P0P2_{benchmark}.json"] = pretty_json_bytes(
            payload
        )
    return outputs


def verify_generated_outputs() -> dict[str, Any]:
    expected = generated_outputs()
    records: list[dict[str, Any]] = []
    for path, payload in expected.items():
        actual = path.read_bytes() if path.is_file() and not path.is_symlink() else None
        records.append(
            {
                "path": relative(path),
                "expected_sha256": hashlib.sha256(payload).hexdigest(),
                "actual_sha256": hashlib.sha256(actual).hexdigest()
                if actual is not None
                else None,
                "byte_identical": actual == payload,
            }
        )
    return {
        "gate_pass": all(record["byte_identical"] for record in records),
        "records": records,
    }


def load_contract() -> dict[str, Any]:
    value = strict_json(CONTRACT_PATH)
    if not isinstance(value, dict) or value != build_contract():
        _fail("P0-P2 machine contract differs from deterministic frozen contract")
    return value


def load_default_denial() -> dict[str, Any]:
    contract = load_contract()
    value = strict_json(AUTHORIZATION_PATH)
    expected = default_authorization(contract)
    if not isinstance(value, dict) or value != expected:
        _fail("P0-P2 authorization is not the exact default denial")
    return value


def schedule_sample_ids() -> tuple[str, ...]:
    return tuple(item["sample_id"] for item in fixed_schedule()["ordered_items"])


def _paths_overlap(first: Path, second: Path) -> bool:
    first_resolved = first.resolve(strict=False)
    second_resolved = second.resolve(strict=False)
    return (
        first_resolved == second_resolved
        or first_resolved.is_relative_to(second_resolved)
        or second_resolved.is_relative_to(first_resolved)
    )


def assert_runtime_input_separation(paths: Sequence[Path]) -> dict[str, bool]:
    """Reject every attempt to use V1 raw or the E0 snapshot as P0-P2 runtime input."""

    observed = tuple(Path(path) for path in paths)
    if not observed:
        _fail("P0-P2 runtime input set must not be empty")
    manifest = strict_json(E0_MANIFEST_PATH)
    if not isinstance(manifest, Mapping):
        _fail("E0 manifest must be one object")
    snapshot = manifest.get("snapshot_copy")
    if not isinstance(snapshot, Mapping) or not isinstance(snapshot.get("files"), list):
        _fail("E0 manifest file identities are missing")
    file_records = snapshot["files"]
    if not (
        len(file_records) == int(snapshot.get("file_count", -1))
        and all(
            isinstance(record, Mapping)
            and isinstance(record.get("sha256"), str)
            and len(record["sha256"]) == 64
            for record in file_records
        )
    ):
        _fail("E0 manifest file hash records are incomplete")
    prohibited_hashes = {record["sha256"] for record in file_records}
    prohibited_roots = (V1_RAW_ROOT, E0_SNAPSHOT_ROOT)
    for path in observed:
        if any(_paths_overlap(path, prohibited) for prohibited in prohibited_roots):
            _fail(f"prohibited V1/E0 runtime input: {relative(path)}")
        if (
            path.is_file()
            and not path.is_symlink()
            and sha256_file(path) in prohibited_hashes
        ):
            _fail(
                f"prohibited V1/E0 byte identity used as runtime input: {relative(path)}"
            )
    return {
        "runtime_input_set_nonempty": True,
        "v1_raw_not_runtime_input": True,
        "e0_snapshot_not_runtime_input": True,
        "v1_file_byte_identities_not_runtime_input": True,
    }


def assert_namespace_separation() -> dict[str, bool]:
    v1_ids = {
        "HF3D_P0_RESULT_BLIND_DIVERGENCE_AND_PERFORMANCE_BASELINE_V1",
        "HF3D_P0P_FIXED_18_V1",
        "HF3D_P0P_DYNAMIC_EXECUTION_AUTHORIZATION_V1",
    }
    paths_disjoint = not _paths_overlap(RAW_ROOT, V1_RAW_ROOT)
    gates = {
        "contract_id_distinct": CONTRACT_ID not in v1_ids,
        "schedule_id_distinct": SCHEDULE_ID not in v1_ids,
        "authorization_id_distinct": AUTHORIZATION_ID not in v1_ids,
        "sample_ids_all_p0p2_prefixed": all(
            value.startswith("P0P2_") for value in schedule_sample_ids()
        ),
        "sample_ids_unique": len(schedule_sample_ids())
        == len(set(schedule_sample_ids()))
        == 18,
        "raw_roots_disjoint": paths_disjoint,
        "claim_and_ledger_namespaces_distinct": all(
            "P0P2" in value for value in (CLAIM_NAMESPACE, LEDGER_SCHEMA_ID)
        ),
    }
    return gates


__all__ = [
    "AUTHORIZATION_ID",
    "AUTHORIZATION_PATH",
    "AUTHORIZED_STATUS",
    "BENCHMARKS",
    "CLAIM_NAMESPACE",
    "CONTRACT_ID",
    "CONTRACT_PATH",
    "DENIED_STATUS",
    "DURATION_S",
    "E0_AUDIT_PATH",
    "E0_CANONICAL_TREE_SHA256",
    "E0_MANIFEST_PATH",
    "E0_SNAPSHOT_ROOT",
    "EFFECTIVE_CONFIG_ROOT",
    "FORMAL_ROOT",
    "FORMAL_SOURCE",
    "FORMAL_SOURCE_SHA256",
    "GPU_PYTHON",
    "GRANT_NONCE_PREFIX",
    "HERE",
    "LEDGER_SCHEMA_ID",
    "LOCK_PATH",
    "OUTPUT_INTERVAL_S",
    "RAW_ROOT",
    "REGISTERED_TIMES_S",
    "REPOSITORY_ROOT",
    "SCHEDULE_ID",
    "STATIC_ROOT",
    "TICKET_PATH",
    "V1_RAW_ROOT",
    "P0P2Error",
    "assert_namespace_separation",
    "assert_runtime_input_separation",
    "build_contract",
    "canonical_json_bytes",
    "canonical_sha256",
    "default_authorization",
    "effective_config",
    "fixed_schedule",
    "generated_outputs",
    "load_contract",
    "load_default_denial",
    "pretty_json_bytes",
    "relative",
    "schedule_sample_ids",
    "sha256_file",
    "source_config_path",
    "strict_json",
    "strict_json_bytes",
    "validate_effective_payload",
    "verify_generated_outputs",
]
