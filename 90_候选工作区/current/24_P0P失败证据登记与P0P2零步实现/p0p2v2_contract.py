"""Frozen identities and pure helpers for the HF3D P0-P2 V2 baseline.

This module intentionally imports only the Python standard library.  The
sample process must let the formal ``airvortex`` package apply its early
thread policy before NumPy, SciPy, or CuPy are imported.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPOSITORY_ROOT = HERE.parents[2]
FORMAL_ROOT = REPOSITORY_ROOT / "02_模拟程序"
FORMAL_SOURCE = FORMAL_ROOT / "src"
D3_ROOT = HERE.parent / "07_D3跨网格时间步响应诊断"
CONTRACT_PATH = HERE / "p0p2v2_contract.json"
EFFECTIVE_CONFIG_ROOT = HERE / "generated_effective_configs"
R1_PREREGISTRATION_PATH = D3_ROOT / "d3_cross_grid_temporal_preregistration_r1.json"
RAW_ROOT = HERE / "artifacts" / "p0p2v2" / "performance_raw"
STATIC_ROOT = HERE / "artifacts" / "p0p2v2" / "static"
LOCK_PATH = HERE / "artifacts" / "p0p2v2" / ".p0p2v2_campaign.lock"
GPU_PYTHON = HERE.parent / "01_HF3D数值资格" / ".venv-gpu" / "bin" / "python"
NSYS_PATH = Path("/opt/nvidia/hpc_sdk/Linux_x86_64/26.5/compilers/bin/nsys")
AUTHORIZATION_PATH = HERE / "p0p2v2_execution_authorization.json"
TICKET_PATH = HERE / "p0p2v2_authorization_ticket.json"
PRO_RULING_PATH = HERE.parent / "25_P0P2零步终审与动态基线实现门裁决.md"
E0_MANIFEST_PATH = HERE / "evidence" / "v1_failure_evidence_manifest.json"
E0_AUDIT_PATH = HERE / "evidence" / "v1_failure_evidence_audit.json"
E0_SNAPSHOT_ROOT = HERE / "evidence" / "v1_claim_failure_snapshot"
V1_RAW_ROOT = HERE.parent / "17_HF3D性能优化" / "artifacts" / "p0" / "performance_raw"

CONTRACT_ID = "HF3D_P0P2_PRODUCTION_EQUIVALENT_PERFORMANCE_BASELINE_V2"
SCHEDULE_ID = "HF3D_P0P2_FIXED_18_V2"
AUTHORIZATION_ID = "HF3D_P0P2_DYNAMIC_EXECUTION_AUTHORIZATION_V2"
CLAIM_NAMESPACE = "HF3D_P0P2_SINGLE_SCHEDULE_CLAIM_V2"
LEDGER_SCHEMA_ID = "HF3D_P0P2_FIXED_18_LEDGER_V2"
SAMPLE_PREFIX = "P0P2V2_"
GRANT_NONCE_PREFIX = "HF3D_P0P2V2_GRANT_NONCE_"
I2_COMMIT = "2bb96803f54a4fb05aeac821a0c580022316809a"
I2_TREE = "a47f86792bc68cfbaddf7d8b67d5d9baa1246d7f"
E0_COMMIT = "379ae8dfb425c2f57e25d2779525fc843592fc98"
PRO_RULING_COMMIT = "90bdb5fdb6b9b737a73be752ba1de4ca567e646a"
PRO_RULING_BLOB = "cb834e71f08a81c966555d94474b968299232c49"
PRO_RULING_SHA256 = "4f76da3ee079734bd610f22f183c04f56b92b66228789c1437c7542ee832fa03"
E0_MANIFEST_SHA256 = "0c2e84e117970cc34ad9ae0793bb5f6edf17090d1da3d064e42ed0abfd3a3e6a"
E0_AUDIT_SHA256 = "2ffb6cc8e2286585604e3be5885f403ef6783fa13b00f1c924251b5b9a1a5860"
R1_PREREGISTRATION_SHA256 = (
    "bc6beda32e942292f5e17bd8236b9f99a2e1966c07ac2b0d620c39612d0a4354"
)
FORMAL_SOURCE_SHA256 = (
    "dcd27cbf4353ab8b0d53802c4810f4423d3a34a45ea2944bdd8346e4eab54bb8"
)
SOURCE_TREE_HASH_DOMAIN = b"airvortex-source-tree-v1\0"
EXPECTED_GPU_PYTHON_VERSIONS = {
    "python": "3.12.3",
    "numpy": "2.5.2",
    "scipy": "1.17.1",
    "cupy": "14.2.0",
    "cupy_distribution": "14.2.0",
}
EXPECTED_SCHEDULE = (
    "P0P2V2_WARMUP_B1",
    "P0P2V2_WARMUP_B2",
    "P0P2V2_WARMUP_B3",
    "P0P2V2_TIMED_R1_B1",
    "P0P2V2_TIMED_R1_B2",
    "P0P2V2_TIMED_R1_B3",
    "P0P2V2_TIMED_R2_B2",
    "P0P2V2_TIMED_R2_B3",
    "P0P2V2_TIMED_R2_B1",
    "P0P2V2_TIMED_R3_B3",
    "P0P2V2_TIMED_R3_B1",
    "P0P2V2_TIMED_R3_B2",
    "P0P2V2_PROFILED_B2",
    "P0P2V2_PROFILED_B3",
)
EXPANDED_EXECUTION_SCHEDULE = (
    *EXPECTED_SCHEDULE[:-2],
    "P0P2V2_PROFILED_B2_HOST",
    "P0P2V2_PROFILED_B2_CUDA",
    "P0P2V2_PROFILED_B2_MEMORY",
    "P0P2V2_PROFILED_B3_HOST",
    "P0P2V2_PROFILED_B3_CUDA",
    "P0P2V2_PROFILED_B3_MEMORY",
)
PROFILE_BUNDLE = {
    "P0P2V2_PROFILED_B2": (
        "P0P2V2_PROFILED_B2_HOST",
        "P0P2V2_PROFILED_B2_CUDA",
        "P0P2V2_PROFILED_B2_MEMORY",
    ),
    "P0P2V2_PROFILED_B3": (
        "P0P2V2_PROFILED_B3_HOST",
        "P0P2V2_PROFILED_B3_CUDA",
        "P0P2V2_PROFILED_B3_MEMORY",
    ),
}
SAMPLE_MODES = ("unprofiled", "host", "cuda", "memory")
EXPECTED_BENCHMARKS = {
    "B1": {
        "source_case": "G6_DT0",
        "source_config_sha256": (
            "602dead5fa6b8b4485103bf91adb53b1bb17b6b3e3f9c8f7c0578be4242d0cd0"
        ),
        "duration_s": 0.005,
        "effective_config_sha256": "e694ea2b4c3b1a686e066cb7f6a919367e7bd1a340f0738964ce4e157389f00b",
    },
    "B2": {
        "source_case": "G9_DT0",
        "source_config_sha256": (
            "64bf8553b88e546b3bd18a8867fd9ed5455be475bb77f467f8c45c157353b86d"
        ),
        "duration_s": 0.005,
        "effective_config_sha256": "d51aed9bb2e90af300d980b00324214cf991aa483c15f4a46a8b2b669d8731c4",
    },
    "B3": {
        "source_case": "G9_DT2",
        "source_config_sha256": (
            "c924a225d4d35b25217318c0bdbe7728bceb637f5474e0eb9e2cbffac6ccdb37"
        ),
        "duration_s": 0.005,
        "effective_config_sha256": "2c3ac702a5e85762eecf2f15bb908f538c697058604d1e5edddad051564618e3",
    },
}
REGISTERED_TIMES_S = (0.0, 0.001, 0.002, 0.003, 0.004, 0.005)
THREAD_ENVIRONMENT_KEYS = (
    "AIRVORTEX_BLAS_THREADS",
    "OPENBLAS_NUM_THREADS",
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "BLIS_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
    "NUMEXPR_NUM_THREADS",
)
EXPECTED_QUALITY_GATES = {
    "accepted_advective_cfl_max": 0.35,
    "all_state_and_diagnostic_values_finite": True,
    "complete_to_registered_end_time": True,
    "configured_time_step_active_fraction_min": 0.95,
    "cumulative_phase_clip_relative_max": 0.001,
    "cumulative_phase_repair_relative_max": 0.001,
    "liquid_volume_balance_error_relative_max": 0.001,
    "maximum_cfl_step_retries": 4,
    "pressure_relative_residual_max": 1e-7,
    "projected_divergence_max_s_inv": 1e-4,
}
P0_D_EVIDENCE_HASHES = {
    E0_MANIFEST_PATH: E0_MANIFEST_SHA256,
    E0_AUDIT_PATH: E0_AUDIT_SHA256,
}
NOT_AUTHORIZED_STATUS = "NOT_AUTHORIZED_PENDING_PRO_STATIC_REVIEW"
AUTHORIZED_STATUS = "AUTHORIZED_BY_PRO_FOR_BOUND_P0P2V2_PARENT_TICKET"


class P0PerformanceError(RuntimeError):
    """Fail-closed P0-P contract, environment, sample, or evidence error."""


def relative(path: Path) -> str:
    return path.resolve().relative_to(REPOSITORY_ROOT.resolve()).as_posix()


def display_path(path: Path) -> str:
    """Use a repository-relative label when possible, otherwise an absolute path."""

    try:
        return relative(path)
    except ValueError:
        return path.absolute().as_posix()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant is prohibited: {value}")


def _no_duplicate_object(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def strict_json(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise P0PerformanceError(f"not a regular JSON file: {display_path(path)}")
    try:
        payload = path.read_bytes()
        return strict_json_bytes(payload, label=display_path(path))
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise P0PerformanceError(f"strict JSON reload failed: {path}") from exc


def strict_json_bytes(payload: bytes, *, label: str) -> dict[str, Any]:
    """Parse one strict UTF-8/LF JSON object from content-addressed bytes."""

    if not payload or payload.startswith(b"\xef\xbb\xbf"):
        raise P0PerformanceError(f"empty or BOM-prefixed JSON is prohibited: {label}")
    if not payload.endswith(b"\n") or b"\r" in payload:
        raise P0PerformanceError(f"JSON must use UTF-8 with LF final newline: {label}")
    try:
        text = payload.decode("utf-8", errors="strict")
        value = json.loads(
            text,
            object_pairs_hook=_no_duplicate_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise P0PerformanceError(f"strict JSON bytes failed: {label}") from exc
    if not isinstance(value, dict):
        raise P0PerformanceError(f"top-level JSON object required: {label}")
    return value


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(
        path,
        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(
                value,
                handle,
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
                allow_nan=False,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_text(path: Path, text: str) -> None:
    """Durably replace one UTF-8 text artifact without exposing a partial file."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def git_text(*arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(REPOSITORY_ROOT), *arguments],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise P0PerformanceError(f"Git command failed for {arguments!r}: {detail}")
    return completed.stdout.rstrip("\n")


def git_bytes(*arguments: str) -> bytes:
    completed = subprocess.run(
        ["git", "-C", str(REPOSITORY_ROOT), *arguments],
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise P0PerformanceError(f"Git command failed for {arguments!r}: {detail}")
    return completed.stdout


def git_is_ancestor(ancestor: str, descendant: str) -> bool:
    completed = subprocess.run(
        [
            "git",
            "-C",
            str(REPOSITORY_ROOT),
            "merge-base",
            "--is-ancestor",
            ancestor,
            descendant,
        ],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    if completed.returncode not in (0, 1):
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise P0PerformanceError(f"Git ancestry check failed: {detail}")
    return completed.returncode == 0


def formal_source_identity() -> dict[str, Any]:
    root = FORMAL_ROOT.resolve()
    if not root.is_dir():
        raise P0PerformanceError("formal program root is unavailable")
    candidates: list[Path] = []
    for relative_name in ("pyproject.toml", "run.py", "run_gui.sh"):
        candidate = root / relative_name
        if candidate.is_file() and not candidate.is_symlink():
            candidates.append(candidate)
    package_root = root / "src" / "airvortex"
    if package_root.is_dir() and not package_root.is_symlink():
        candidates.extend(
            path
            for path in package_root.rglob("*.py")
            if path.is_file() and not path.is_symlink()
        )
    files = sorted(candidates, key=lambda item: item.relative_to(root).as_posix())
    if not files:
        raise P0PerformanceError("formal source-tree allow-list is empty")
    digest = hashlib.sha256()
    digest.update(SOURCE_TREE_HASH_DOMAIN)
    total_bytes = 0
    for path in files:
        relative_bytes = path.relative_to(root).as_posix().encode("utf-8")
        payload = path.read_bytes()
        digest.update(len(relative_bytes).to_bytes(8, "big"))
        digest.update(relative_bytes)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
        total_bytes += len(payload)
    return {
        "algorithm": "sha256",
        "sha256": digest.hexdigest(),
        "file_count": len(files),
        "total_bytes": total_bytes,
    }


def _frozen_numerical_quality_gates() -> dict[str, Any]:
    if sha256_file(R1_PREREGISTRATION_PATH) != R1_PREREGISTRATION_SHA256:
        raise P0PerformanceError("D3R1 preregistration identity drifted")
    r1 = strict_json(R1_PREREGISTRATION_PATH)
    quality = r1.get("numerical_quality_hard_gates")
    if quality != EXPECTED_QUALITY_GATES:
        raise P0PerformanceError("P0-P2 V2 and D3R1 numerical hard gates differ")
    return dict(EXPECTED_QUALITY_GATES)


def _logical_sample_identity(sample_id: str) -> dict[str, str]:
    if not sample_id.startswith(SAMPLE_PREFIX):
        raise P0PerformanceError(f"sample lacks the V2 namespace: {sample_id}")
    logical = sample_id.removeprefix(SAMPLE_PREFIX)
    if logical.startswith("WARMUP_"):
        disposition = "warmup"
        benchmark = logical.removeprefix("WARMUP_")
        mode = "unprofiled"
    elif logical.startswith("PROFILED_"):
        disposition = "profiled"
        parts = logical.split("_")
        if len(parts) != 3:
            raise P0PerformanceError(f"expanded profile sample is invalid: {sample_id}")
        benchmark = parts[1]
        mode = parts[2].lower()
    else:
        disposition = "timed"
        benchmark = logical.rsplit("_", maxsplit=1)[-1]
        mode = "unprofiled"
    if benchmark not in EXPECTED_BENCHMARKS or mode not in SAMPLE_MODES:
        raise P0PerformanceError(f"sample identity is invalid: {sample_id}")
    return {
        "sample_id": sample_id,
        "benchmark": benchmark,
        "disposition": disposition,
        "mode": mode,
    }


def fixed_schedule_manifest() -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for sequence_index, sample_id in enumerate(EXPANDED_EXECUTION_SCHEDULE):
        identity = _logical_sample_identity(sample_id)
        benchmark = identity["benchmark"]
        items.append(
            {
                "sequence_index": sequence_index,
                **identity,
                "scored": identity["disposition"] == "timed",
                "source_config_sha256": EXPECTED_BENCHMARKS[benchmark][
                    "source_config_sha256"
                ],
                "effective_config_sha256": EXPECTED_BENCHMARKS[benchmark][
                    "effective_config_sha256"
                ],
                "duration_s": 0.005,
                "registered_output_times_s": list(REGISTERED_TIMES_S),
            }
        )
    unsigned = {
        "schema_version": 2,
        "schedule_id": SCHEDULE_ID,
        "sample_namespace": SAMPLE_PREFIX,
        "sample_reorder": "PROHIBITED",
        "sample_replacement": "PROHIBITED",
        "schedule_resume_after_claim": "PROHIBITED",
        "schedule_retry_or_supplement": "PROHIBITED",
        "ordered_items": items,
    }
    return {**unsigned, "canonical_schedule_sha256": canonical_sha256(unsigned)}


def build_contract() -> dict[str, Any]:
    if git_text("rev-parse", f"{I2_COMMIT}^{{tree}}") != I2_TREE:
        raise P0PerformanceError("accepted I2 commit tree identity drifted")
    ruling_relative = relative(PRO_RULING_PATH)
    if not PRO_RULING_PATH.is_file() or PRO_RULING_PATH.is_symlink():
        raise P0PerformanceError("P0-P2 V2 Pro ruling is absent")
    if (
        sha256_file(PRO_RULING_PATH) != PRO_RULING_SHA256
        or git_text("rev-parse", f"{PRO_RULING_COMMIT}:{ruling_relative}")
        != PRO_RULING_BLOB
        or git_bytes("show", f"{PRO_RULING_COMMIT}:{ruling_relative}")
        != PRO_RULING_PATH.read_bytes()
    ):
        raise P0PerformanceError("P0-P2 V2 Pro ruling identity drifted")
    if sha256_file(E0_MANIFEST_PATH) != E0_MANIFEST_SHA256:
        raise P0PerformanceError("E0 manifest identity drifted")
    if sha256_file(E0_AUDIT_PATH) != E0_AUDIT_SHA256:
        raise P0PerformanceError("E0 audit identity drifted")
    source = formal_source_identity()
    if source["sha256"] != FORMAL_SOURCE_SHA256:
        raise P0PerformanceError("formal source-tree identity drifted")
    benchmarks: dict[str, Any] = {}
    for benchmark, definition in EXPECTED_BENCHMARKS.items():
        _payload, identity = effective_config(benchmark)
        benchmarks[benchmark] = {
            **definition,
            "only_allowed_config_change": "run.duration_s",
            "effective_identity": identity,
        }
    schedule = fixed_schedule_manifest()
    return {
        "schema_version": 2,
        "contract_id": CONTRACT_ID,
        "schedule_id": SCHEDULE_ID,
        "status": "FROZEN_DEFAULT_DENIAL_BEFORE_ANY_P0P2V2_DYNAMIC_SAMPLE",
        "purpose": "PRODUCTION_EQUIVALENT_ENGINEERING_PERFORMANCE_BASELINE_ONLY",
        "binding": {
            "accepted_i2_commit": I2_COMMIT,
            "accepted_i2_tree": I2_TREE,
            "e0_commit": E0_COMMIT,
            "pro_ruling_commit": PRO_RULING_COMMIT,
            "pro_ruling_path": ruling_relative,
            "pro_ruling_git_blob": PRO_RULING_BLOB,
            "pro_ruling_sha256": PRO_RULING_SHA256,
            "formal_source_tree_sha256": FORMAL_SOURCE_SHA256,
            "formal_source_tree": source,
        },
        "p0_d": {
            "role": "NUMERICAL_HARD_GATES_ONLY__NO_RESULT_REUSE",
            "frozen_limits": _frozen_numerical_quality_gates(),
        },
        "p0_p": {
            "benchmark_definitions": benchmarks,
            "output_interval_s": 0.001,
            "required_registered_times_s": list(REGISTERED_TIMES_S),
            "execution_schedule": list(EXPECTED_SCHEDULE),
            "expanded_execution_schedule": list(EXPANDED_EXECUTION_SCHEDULE),
            "fixed_schedule": schedule,
            "process_policy": {
                "fresh_process_per_sample": True,
                "single_HF3D_writer": True,
                "claim_before_first_sample": True,
                "preflight_consumes_claim": False,
                "failure_policy": "FAILED_STOPPED_NO_RESUME",
                "sample_replacement": "PROHIBITED",
                "retry_resume_skip_only_start_at": "PROHIBITED",
            },
            "frozen_thread_environment": {
                "AIRVORTEX_BLAS_THREADS": "unset, selecting formal auto policy",
                "selected_threads": 2,
                "OMP_DYNAMIC": "FALSE",
                "library_specific_thread_variables": (
                    "unset before Python startup so the formal auto policy applies 2 "
                    "to each supported library"
                ),
            },
            "environment_start_gates": {
                "initial_instantaneous_gate_before_zero_step_allocation": True,
                "fresh_process_zero_step_B1_B2_B3": True,
                "post_probe_background_observation_s": 30.0,
                "background_cpu_percent_max_one_logical_cpu_basis": 25.0,
                "cpu_tctl_start_max_c": 90.0,
                "nvidia_gpu_start_max_c": 75.0,
                "no_other_compute_or_HF3D_process": True,
                "no_other_GPU_compute_process": True,
                "ac_power_online": True,
                "online_logical_cpus": 32,
            },
            "end_to_end_timing_boundary": [
                "fresh process launch and imports",
                "thread policy and identity/config gates",
                "grid pressure initial state construction",
                "all accepted/rejected trials and numerical hard gates",
                "registered frames and result assembly",
                "archive write and strict reload",
                "canonical numerical payload extraction",
                "final CUDA synchronization and normal process exit",
            ],
            "scored_policy": {
                "profiler_or_allocator_interposer": "PROHIBITED",
                "external_read_only_telemetry": "REQUIRED",
                "per_benchmark_n": 3,
                "outlier_exclusion": "PROHIBITED",
            },
            "profile_policy": {
                "HOST": {
                    "cProfile": True,
                    "transparent_operator_ranges": True,
                    "top_level_coverage_min": 0.98,
                    "named_solver_exclusive_coverage_min": 0.90,
                    "unknown_total_max": 0.10,
                    "single_unknown_region_max": 0.05,
                },
                "CUDA": {
                    "nsys_version": "2026.2.1",
                    "trace": "cuda,nvtx",
                    "sample": "none",
                    "cpuctxsw": "none",
                    "osrt": "PROHIBITED",
                    "cuda_memory_usage": "PROHIBITED",
                    "sampling_backtrace_metrics_um": "PROHIBITED",
                    "required_stats": [
                        "cuda_api_sum",
                        "cuda_gpu_kern_sum",
                        "cuda_gpu_mem_time_sum",
                        "cuda_gpu_mem_size_sum",
                    ],
                },
                "MEMORY": {
                    "native_allocator_observer": True,
                    "cupy_allocation_recorder": True,
                    "pool_high_water": True,
                    "cuda_allocation_api_crosscheck": True,
                    "rss_vmhwm": True,
                    "device_memory_timeline": True,
                },
            },
            "cross_mode_numerical_equality": {
                "comparison": "BITWISE_CANONICAL_PAYLOAD_SHA256",
                "required_modes": ["TIMED", "HOST", "CUDA", "MEMORY"],
                "fields": [
                    "accepted/rejected sequence",
                    "dt/limiter/retry",
                    "pressure iterations",
                    "registered time uint64",
                    "archive state arrays",
                    "diagnostic arrays",
                    "observable arrays",
                ],
            },
            "raw_output_root": relative(RAW_ROOT),
            "static_output_root": relative(STATIC_ROOT),
        },
        "identity_namespaces": {
            "authorization_id": AUTHORIZATION_ID,
            "claim_namespace": CLAIM_NAMESPACE,
            "ledger_schema_id": LEDGER_SCHEMA_ID,
            "sample_prefix": SAMPLE_PREFIX,
            "grant_nonce_prefix": GRANT_NONCE_PREFIX,
        },
        "authorization_topology": {
            "order": "I2V2 -> P2 -> C2",
            "P2_exactly_one_parent": True,
            "symbolic_HEAD": "refs/heads/main",
            "fetch_before_audit": "git fetch --no-tags origin main",
            "raw_diff": "NUL_SAFE_STATUS_MODE_OLD_NEW_BLOB",
            "review_metadata_mode": "100644",
            "synthetic_git_positive_and_negative_tests": True,
        },
        "runtime_input_policy": {
            "old_v1_candidate_modules_imported": False,
            "v1_raw_as_numerical_input": "PROHIBITED",
            "e0_snapshot_as_numerical_input": "PROHIBITED",
            "source_configs": [relative(source_config_path(b)) for b in EXPECTED_BENCHMARKS],
            "formal_source": relative(FORMAL_SOURCE),
        },
        "paths": {
            "contract": relative(CONTRACT_PATH),
            "authorization": relative(AUTHORIZATION_PATH),
            "future_ticket": relative(TICKET_PATH),
            "raw_root": relative(RAW_ROOT),
            "lock": relative(LOCK_PATH),
            "static_root": relative(STATIC_ROOT),
            "effective_config_root": relative(EFFECTIVE_CONFIG_ROOT),
        },
        "current_authority": {
            "dynamic_harness_implementation": True,
            "dynamic_samples": False,
            "parent_ticket_issuance": False,
            "authorization_grant": False,
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


def pretty_json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        + "\n"
    ).encode("utf-8")


def generated_contract_bytes() -> bytes:
    return pretty_json_bytes(build_contract())


def load_contract() -> dict[str, Any]:
    contract = strict_json(CONTRACT_PATH)
    expected = build_contract()
    if contract != expected:
        raise P0PerformanceError("P0-P2 V2 contract differs from deterministic build")
    return contract


def numerical_quality_gates() -> dict[str, Any]:
    return _frozen_numerical_quality_gates()


def execution_binding(paths: Sequence[Path]) -> dict[str, Any]:
    contract = load_contract()
    branch = git_text("branch", "--show-current")
    symbolic_head = git_text("symbolic-ref", "-q", "HEAD")
    head = git_text("rev-parse", "HEAD")
    origin_main = git_text("rev-parse", "origin/main")
    status = git_text("status", "--porcelain=v1", "--untracked-files=all")
    if (
        branch != "main"
        or symbolic_head != "refs/heads/main"
        or head != origin_main
        or status
    ):
        raise P0PerformanceError(
            "P0-P2 V2 execution requires clean symbolic main equal to origin/main"
        )
    if not git_is_ancestor(I2_COMMIT, head) or not git_is_ancestor(
        PRO_RULING_COMMIT, head
    ):
        raise P0PerformanceError("accepted I2 and Pro ruling must be ancestors of HEAD")
    contract_relative = relative(CONTRACT_PATH)
    if git_bytes("show", f"HEAD:{contract_relative}") != CONTRACT_PATH.read_bytes():
        raise P0PerformanceError("working P0-P2 V2 contract differs from HEAD")
    path_records: dict[str, Any] = {}
    for path in paths:
        resolved = path.resolve()
        relative_path = relative(resolved)
        git_text("ls-files", "--error-unmatch", "--", relative_path)
        if git_bytes("show", f"HEAD:{relative_path}") != resolved.read_bytes():
            raise P0PerformanceError(f"tracked implementation drifted: {relative_path}")
        path_records[relative_path] = {
            "sha256": sha256_file(resolved),
            "git_blob_sha": git_text("rev-parse", f"HEAD:{relative_path}"),
        }
    source = formal_source_identity()
    if source["sha256"] != FORMAL_SOURCE_SHA256:
        raise P0PerformanceError("formal source-tree identity drifted")
    for benchmark, definition in EXPECTED_BENCHMARKS.items():
        source_path = source_config_path(benchmark)
        if sha256_file(source_path) != definition["source_config_sha256"]:
            raise P0PerformanceError(f"{benchmark} source configuration drifted")
    return {
        "branch": branch,
        "symbolic_head": symbolic_head,
        "execution_commit": head,
        "origin_main": origin_main,
        "worktree_clean": True,
        "contract_sha256": sha256_file(CONTRACT_PATH),
        "contract_id": contract["contract_id"],
        "formal_source_tree": source,
        "implementation_files": path_records,
    }


def evidence_identity() -> dict[str, Any]:
    """Verify E0 and Pro-ruling inputs without using V1 raw as numerical input."""

    gates: dict[str, bool] = {}
    evidence: dict[str, Any] = {}
    for path, expected_hash in P0_D_EVIDENCE_HASHES.items():
        exists = path.is_file() and not path.is_symlink()
        observed = sha256_file(path) if exists else None
        label = relative(path)
        gates[label] = exists and observed == expected_hash
        evidence[label] = {
            "expected_sha256": expected_hash,
            "observed_sha256": observed,
            "regular_file_not_symlink": exists,
        }
    ruling_relative = relative(PRO_RULING_PATH)
    ruling_matches_commit = False
    if PRO_RULING_PATH.is_file() and not PRO_RULING_PATH.is_symlink():
        try:
            ruling_matches_commit = (
                git_bytes("show", f"{PRO_RULING_COMMIT}:{ruling_relative}")
                == PRO_RULING_PATH.read_bytes()
            )
        except P0PerformanceError:
            ruling_matches_commit = False
    gates["pro_ruling_blob"] = ruling_matches_commit
    gates["pro_ruling_blob_oid"] = (
        git_text("rev-parse", f"{PRO_RULING_COMMIT}:{ruling_relative}")
        == PRO_RULING_BLOB
    )
    gates["accepted_i2_tree"] = git_text("rev-parse", f"{I2_COMMIT}^{{tree}}") == I2_TREE
    failed = [name for name, passed in gates.items() if not passed]
    if failed:
        raise P0PerformanceError(
            "P0-P frozen evidence identity failed: " + ", ".join(failed)
        )
    return {
        "gate_pass": True,
        "gates": gates,
        "e0_evidence": evidence,
        "pro_ruling_commit": PRO_RULING_COMMIT,
        "pro_ruling_path": ruling_relative,
        "accepted_i2_commit": I2_COMMIT,
        "accepted_i2_tree": I2_TREE,
    }


def source_config_path(benchmark: str) -> Path:
    definition = EXPECTED_BENCHMARKS.get(benchmark)
    if definition is None:
        raise P0PerformanceError(f"unknown benchmark: {benchmark}")
    return D3_ROOT / "generated_configs" / f"{definition['source_case']}.json"


def _leaf_differences(
    baseline: object,
    effective: object,
    prefix: str = "",
) -> dict[str, dict[str, Any]]:
    differences: dict[str, dict[str, Any]] = {}
    if isinstance(baseline, Mapping) and isinstance(effective, Mapping):
        keys = sorted(set(baseline) | set(effective))
        for key in keys:
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


def effective_config(benchmark: str) -> tuple[dict[str, Any], dict[str, Any]]:
    definition = EXPECTED_BENCHMARKS.get(benchmark)
    if definition is None:
        raise P0PerformanceError(f"unknown benchmark: {benchmark}")
    source_path = source_config_path(benchmark)
    if sha256_file(source_path) != definition["source_config_sha256"]:
        raise P0PerformanceError(f"{benchmark} source configuration hash drifted")
    baseline = strict_json(source_path)
    effective = deepcopy(baseline)
    run = effective.get("run")
    if not isinstance(run, dict) or "duration_s" not in run:
        raise P0PerformanceError(f"{benchmark} source run.duration_s is missing")
    baseline_duration = run["duration_s"]
    run["duration_s"] = definition["duration_s"]
    differences = _leaf_differences(baseline, effective)
    expected_difference = {
        "run.duration_s": {
            "baseline": baseline_duration,
            "effective": definition["duration_s"],
        }
    }
    if differences != expected_difference:
        raise P0PerformanceError(f"{benchmark} effective config changed extra fields")
    identity = validate_effective_payload(benchmark, effective)
    return effective, identity


def validate_effective_payload(
    benchmark: str, payload: Mapping[str, Any]
) -> dict[str, Any]:
    definition = EXPECTED_BENCHMARKS.get(benchmark)
    if definition is None:
        raise P0PerformanceError(f"unknown benchmark: {benchmark}")
    source_path = source_config_path(benchmark)
    baseline = strict_json(source_path)
    observed = deepcopy(dict(payload))
    differences = _leaf_differences(baseline, observed)
    expected_difference = {
        "run.duration_s": {
            "baseline": baseline["run"]["duration_s"],
            "effective": 0.005,
        }
    }
    run = observed.get("run")
    drive = observed.get("drive")
    if not (
        differences == expected_difference
        and isinstance(run, Mapping)
        and isinstance(drive, Mapping)
        and run.get("duration_s") == drive.get("ramp_duration_s") == 0.005
        and run.get("output_interval_s") == 0.001
        and drive.get("actual_speed_schedule_time_s") in ([], ())
    ):
        raise P0PerformanceError(
            f"{benchmark} effective payload violates the 0.005 registered-window contract"
        )
    effective_sha256 = canonical_sha256(observed)
    if effective_sha256 != definition["effective_config_sha256"]:
        raise P0PerformanceError(
            f"{benchmark} effective configuration canonical hash drifted"
        )
    return {
        "benchmark": benchmark,
        "source_case": definition["source_case"],
        "source_path": relative(source_path),
        "source_file_sha256": definition["source_config_sha256"],
        "source_canonical_sha256": canonical_sha256(baseline),
        "effective_canonical_sha256": effective_sha256,
        "differences": differences,
    }


def sample_identity(sample_id: str) -> dict[str, str]:
    if sample_id not in EXPANDED_EXECUTION_SCHEDULE:
        raise P0PerformanceError(f"sample is outside the frozen schedule: {sample_id}")
    return _logical_sample_identity(sample_id)


def _paths_overlap(first: Path, second: Path) -> bool:
    first_resolved = first.resolve(strict=False)
    second_resolved = second.resolve(strict=False)
    return (
        first_resolved == second_resolved
        or first_resolved.is_relative_to(second_resolved)
        or second_resolved.is_relative_to(first_resolved)
    )


def assert_runtime_input_separation(paths: Sequence[Path]) -> dict[str, bool]:
    """Reject V1 raw/E0 paths and copied E0 file bytes as V2 numerical input."""

    observed = tuple(Path(path) for path in paths)
    if not observed:
        raise P0PerformanceError("P0-P2 V2 runtime input set must not be empty")
    manifest = strict_json(E0_MANIFEST_PATH)
    snapshot = manifest.get("snapshot_copy")
    if not isinstance(snapshot, Mapping) or not isinstance(snapshot.get("files"), list):
        raise P0PerformanceError("E0 snapshot file identities are unavailable")
    file_records = snapshot["files"]
    if not (
        len(file_records) == int(snapshot.get("file_count", -1))
        and all(
            isinstance(record, Mapping)
            and isinstance(record.get("sha256"), str)
            and len(str(record["sha256"])) == 64
            for record in file_records
        )
    ):
        raise P0PerformanceError("E0 snapshot file identities are incomplete")
    prohibited_hashes = {str(record["sha256"]) for record in file_records}
    prohibited_roots = (V1_RAW_ROOT, E0_SNAPSHOT_ROOT)
    checked_paths: list[str] = []
    for path in observed:
        if any(_paths_overlap(path, root) for root in prohibited_roots):
            raise P0PerformanceError(
                f"prohibited V1/E0 runtime input path: {display_path(path)}"
            )
        if (
            path.is_file()
            and not path.is_symlink()
            and sha256_file(path) in prohibited_hashes
        ):
            raise P0PerformanceError(
                "prohibited V1/E0 byte identity used as runtime input: "
                f"{display_path(path)}"
            )
        checked_paths.append(display_path(path))
    return {
        "runtime_input_set_nonempty": True,
        "v1_raw_not_runtime_input": True,
        "e0_snapshot_not_runtime_input": True,
        "v1_e0_file_byte_identities_not_runtime_input": True,
        "all_paths_checked": len(checked_paths) == len(observed),
    }


def validate_schedule_prefix(completed: Sequence[str], requested: str) -> int:
    """Require an exact, unique prefix before the next frozen sample."""

    completed_tuple = tuple(completed)
    expected_prefix = EXPANDED_EXECUTION_SCHEDULE[: len(completed_tuple)]
    if completed_tuple != expected_prefix:
        raise P0PerformanceError(
            "completed sample ledger is not the exact frozen schedule prefix"
        )
    if len(set(completed_tuple)) != len(completed_tuple):
        raise P0PerformanceError("completed sample ledger contains a duplicate")
    if len(completed_tuple) >= len(EXPANDED_EXECUTION_SCHEDULE):
        raise P0PerformanceError("frozen schedule is already complete")
    expected_next = EXPANDED_EXECUTION_SCHEDULE[len(completed_tuple)]
    if requested != expected_next:
        raise P0PerformanceError(
            f"next sample must be {expected_next}, not {requested}"
        )
    return len(completed_tuple)


def load_authorization(*, require_dynamic: bool) -> dict[str, Any]:
    """Load the denial or fully audited parent-ticket authorization."""

    from p0p2v2_authorization import load_authorization as load_parent_ticket

    return load_parent_ticket(require_dynamic=require_dynamic)


def assert_no_sample_output_root() -> dict[str, Any]:
    """Require the raw root to be absent or an empty, real directory."""

    if RAW_ROOT.is_symlink():
        raise P0PerformanceError("performance raw root must never be a symlink")
    if not RAW_ROOT.exists():
        return {"exists": False, "empty": True, "path": relative(RAW_ROOT)}
    if not RAW_ROOT.is_dir():
        raise P0PerformanceError("performance raw root is not a directory")
    if any(RAW_ROOT.iterdir()):
        raise P0PerformanceError("performance raw root is not empty")
    return {"exists": True, "empty": True, "path": relative(RAW_ROOT)}


def frozen_child_environment(source: Mapping[str, str]) -> dict[str, str]:
    environment = dict(source)
    for key in THREAD_ENVIRONMENT_KEYS:
        environment.pop(key, None)
    environment["OMP_DYNAMIC"] = "FALSE"
    return environment


__all__ = [
    "AUTHORIZATION_ID",
    "AUTHORIZATION_PATH",
    "AUTHORIZED_STATUS",
    "CLAIM_NAMESPACE",
    "CONTRACT_ID",
    "CONTRACT_PATH",
    "E0_AUDIT_PATH",
    "E0_MANIFEST_PATH",
    "E0_SNAPSHOT_ROOT",
    "EFFECTIVE_CONFIG_ROOT",
    "EXPANDED_EXECUTION_SCHEDULE",
    "EXPECTED_BENCHMARKS",
    "EXPECTED_GPU_PYTHON_VERSIONS",
    "EXPECTED_QUALITY_GATES",
    "EXPECTED_SCHEDULE",
    "FORMAL_ROOT",
    "FORMAL_SOURCE",
    "FORMAL_SOURCE_SHA256",
    "GPU_PYTHON",
    "GRANT_NONCE_PREFIX",
    "I2_COMMIT",
    "I2_TREE",
    "LEDGER_SCHEMA_ID",
    "LOCK_PATH",
    "NOT_AUTHORIZED_STATUS",
    "NSYS_PATH",
    "P0_D_EVIDENCE_HASHES",
    "PROFILE_BUNDLE",
    "PRO_RULING_BLOB",
    "PRO_RULING_COMMIT",
    "PRO_RULING_PATH",
    "PRO_RULING_SHA256",
    "R1_PREREGISTRATION_PATH",
    "RAW_ROOT",
    "REGISTERED_TIMES_S",
    "SAMPLE_MODES",
    "SAMPLE_PREFIX",
    "SCHEDULE_ID",
    "STATIC_ROOT",
    "TICKET_PATH",
    "V1_RAW_ROOT",
    "P0PerformanceError",
    "assert_no_sample_output_root",
    "assert_runtime_input_separation",
    "atomic_json",
    "atomic_text",
    "build_contract",
    "canonical_json_bytes",
    "canonical_sha256",
    "display_path",
    "effective_config",
    "evidence_identity",
    "execution_binding",
    "fixed_schedule_manifest",
    "formal_source_identity",
    "frozen_child_environment",
    "generated_contract_bytes",
    "git_text",
    "load_authorization",
    "load_contract",
    "numerical_quality_gates",
    "pretty_json_bytes",
    "relative",
    "sample_identity",
    "sha256_file",
    "strict_json",
    "strict_json_bytes",
    "validate_effective_payload",
    "validate_schedule_prefix",
]
