"""Frozen identities and pure helpers for the HF3D P0-P baseline.

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
CONTRACT_PATH = HERE / "00_P0性能与散度诊断预注册.json"
R1_PREREGISTRATION_PATH = D3_ROOT / "d3_cross_grid_temporal_preregistration_r1.json"
RAW_ROOT = HERE / "artifacts" / "p0" / "performance_raw"
STATIC_ROOT = HERE / "artifacts" / "p0" / "p0p_static"
GPU_PYTHON = HERE.parent / "01_HF3D数值资格" / ".venv-gpu" / "bin" / "python"
NSYS_PATH = Path("/opt/nvidia/hpc_sdk/Linux_x86_64/26.5/compilers/bin/nsys")
AUTHORIZATION_PATH = HERE / "p0p_execution_authorization.json"
PRO_RULING_PATH = HERE.parent / "19_HF3DP0P最终规划与证据充分性裁决.md"
AUTH_STRUCTURE_RULING_PATH = HERE.parent / "20_HF3DP0P无自引用精确授权结构裁决.md"

CONTRACT_ID = "HF3D_P0_RESULT_BLIND_DIVERGENCE_AND_PERFORMANCE_BASELINE_V1"
CONTRACT_COMMIT = "246a4d5c365b022ad4bd2e930393bacdd59fef61"
PRO_RULING_COMMIT = "d12078f996092c82f46936b594c7d6d645ccdda0"
AUTH_STRUCTURE_RULING_COMMIT = "2ec92aa0646f403bfd813a7721c52073f83dc93f"
CONTRACT_SHA256 = "4952d0d0cdedc7dadf992b9304ac6936867a5240ed17b5ac3d0048e510a56085"
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
    "WARMUP_B1",
    "WARMUP_B2",
    "WARMUP_B3",
    "TIMED_R1_B1",
    "TIMED_R1_B2",
    "TIMED_R1_B3",
    "TIMED_R2_B2",
    "TIMED_R2_B3",
    "TIMED_R2_B1",
    "TIMED_R3_B3",
    "TIMED_R3_B1",
    "TIMED_R3_B2",
    "PROFILED_B2",
    "PROFILED_B3",
)
EXPANDED_EXECUTION_SCHEDULE = (
    *EXPECTED_SCHEDULE[:-2],
    "PROFILED_B2_HOST",
    "PROFILED_B2_CUDA",
    "PROFILED_B2_MEMORY",
    "PROFILED_B3_HOST",
    "PROFILED_B3_CUDA",
    "PROFILED_B3_MEMORY",
)
PROFILE_BUNDLE = {
    "PROFILED_B2": (
        "PROFILED_B2_HOST",
        "PROFILED_B2_CUDA",
        "PROFILED_B2_MEMORY",
    ),
    "PROFILED_B3": (
        "PROFILED_B3_HOST",
        "PROFILED_B3_CUDA",
        "PROFILED_B3_MEMORY",
    ),
}
SAMPLE_MODES = ("unprofiled", "host", "cuda", "memory")
EXPECTED_BENCHMARKS = {
    "B1": {
        "source_case": "G6_DT0",
        "source_config_sha256": (
            "602dead5fa6b8b4485103bf91adb53b1bb17b6b3e3f9c8f7c0578be4242d0cd0"
        ),
        "duration_s": 0.002,
    },
    "B2": {
        "source_case": "G9_DT0",
        "source_config_sha256": (
            "64bf8553b88e546b3bd18a8867fd9ed5455be475bb77f467f8c45c157353b86d"
        ),
        "duration_s": 0.002,
    },
    "B3": {
        "source_case": "G9_DT2",
        "source_config_sha256": (
            "c924a225d4d35b25217318c0bdbe7728bceb637f5474e0eb9e2cbffac6ccdb37"
        ),
        "duration_s": 0.002,
    },
}
REGISTERED_TIMES_S = (0.0, 0.001, 0.002)
THREAD_ENVIRONMENT_KEYS = (
    "AIRVORTEX_BLAS_THREADS",
    "OPENBLAS_NUM_THREADS",
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "BLIS_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
    "NUMEXPR_NUM_THREADS",
)
P0_D_EVIDENCE_HASHES = {
    HERE / "artifacts" / "p0" / "divergence_access_ledger.json": (
        "d6c380cc09e3eada938df9dfa9deab9106018511f935bbd196afe5d9e2ecbd0f"
    ),
    HERE / "artifacts" / "p0" / "divergence_diagnostics.json": (
        "d83f3bd42b819cdfadea51dd2153f183eee7f7d29fbde6111722fb6abc0cffa5"
    ),
    HERE / "artifacts" / "p0" / "divergence_diagnostics.md": (
        "ec0be83f071ed5b60c6dd84024e3414583a7e03d38ef0b60d15f360267ccec42"
    ),
}
AUTHORIZATION_ID = "HF3D_P0P_DYNAMIC_EXECUTION_AUTHORIZATION_V1"
NOT_AUTHORIZED_STATUS = "NOT_AUTHORIZED_PENDING_PRO_STATIC_REVIEW"
AUTHORIZED_STATUS = "AUTHORIZED_BY_PRO_FOR_BOUND_PARENT_TICKET"


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


def load_contract() -> dict[str, Any]:
    contract = strict_json(CONTRACT_PATH)
    performance = contract.get("p0_p")
    if not isinstance(performance, Mapping):
        raise P0PerformanceError("P0-P contract object is missing")
    expected_definitions = {
        key: {
            **value,
            "only_allowed_config_change": "run.duration_s",
        }
        for key, value in EXPECTED_BENCHMARKS.items()
    }
    gates = {
        "contract_id": contract.get("contract_id") == CONTRACT_ID,
        "status": contract.get("status")
        == "FROZEN_BEFORE_DIAGNOSTIC_VALUES_OR_BENCHMARK_EXECUTION",
        "contract_sha256": sha256_file(CONTRACT_PATH) == CONTRACT_SHA256,
        "formal_source": contract.get("binding", {}).get("formal_source_tree_sha256")
        == FORMAL_SOURCE_SHA256,
        "benchmark_definitions": performance.get("benchmark_definitions")
        == expected_definitions,
        "registered_times": performance.get("required_registered_times_s")
        == list(REGISTERED_TIMES_S),
        "output_interval": performance.get("output_interval_s") == 0.001,
        "schedule": performance.get("execution_schedule") == list(EXPECTED_SCHEDULE),
        "fresh_process": performance.get("process_policy", {}).get(
            "fresh_process_per_sample"
        )
        is True,
        "single_writer": performance.get("process_policy", {}).get("single_HF3D_writer")
        is True,
        "no_replacement": performance.get("report_statistics", {}).get(
            "no_sample_replacement"
        )
        is True,
        "raw_root": performance.get("raw_output_root") == relative(RAW_ROOT),
        "thread_policy": performance.get("frozen_thread_environment")
        == {
            "AIRVORTEX_BLAS_THREADS": "unset, selecting formal auto policy",
            "selected_threads": 2,
            "OMP_DYNAMIC": "FALSE",
            "library_specific_thread_variables": (
                "unset before Python startup so the formal auto policy applies 2 "
                "to each supported library"
            ),
        },
    }
    failed = [name for name, passed in gates.items() if not passed]
    if failed:
        raise P0PerformanceError(
            "P0-P frozen contract validation failed: " + ", ".join(failed)
        )
    return contract


def numerical_quality_gates() -> dict[str, Any]:
    contract = load_contract()
    if sha256_file(R1_PREREGISTRATION_PATH) != R1_PREREGISTRATION_SHA256:
        raise P0PerformanceError("D3R1 preregistration identity drifted")
    r1 = strict_json(R1_PREREGISTRATION_PATH)
    quality = r1.get("numerical_quality_hard_gates")
    expected = dict(contract["p0_d"]["frozen_limits"])
    expected.update(
        {
            "all_state_and_diagnostic_values_finite": True,
            "complete_to_registered_end_time": True,
            "configured_time_step_active_fraction_min": 0.95,
            "cumulative_phase_clip_relative_max": 0.001,
            "cumulative_phase_repair_relative_max": 0.001,
            "liquid_volume_balance_error_relative_max": 0.001,
        }
    )
    if quality != expected:
        raise P0PerformanceError("P0 and D3R1 numerical hard gates differ")
    return expected


def execution_binding(paths: Sequence[Path]) -> dict[str, Any]:
    contract = load_contract()
    branch = git_text("branch", "--show-current")
    head = git_text("rev-parse", "HEAD")
    origin_main = git_text("rev-parse", "origin/main")
    status = git_text("status", "--porcelain=v1", "--untracked-files=all")
    if branch != "main" or head != origin_main or status:
        raise P0PerformanceError(
            "P0-P execution requires clean local main equal to origin/main"
        )
    if not git_is_ancestor(CONTRACT_COMMIT, head):
        raise P0PerformanceError("P0 contract commit is not an ancestor of HEAD")
    contract_relative = relative(CONTRACT_PATH)
    if (
        git_bytes("show", f"{CONTRACT_COMMIT}:{contract_relative}")
        != CONTRACT_PATH.read_bytes()
    ):
        raise P0PerformanceError("working P0 contract differs from the frozen blob")
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
        "execution_commit": head,
        "origin_main": origin_main,
        "worktree_clean": True,
        "contract_commit": CONTRACT_COMMIT,
        "contract_sha256": CONTRACT_SHA256,
        "contract_id": contract["contract_id"],
        "formal_source_tree": source,
        "implementation_files": path_records,
    }


def evidence_identity() -> dict[str, Any]:
    """Verify immutable P0-D and Pro-plan inputs without reading D3 raw values."""

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
    authorization_ruling_relative = relative(AUTH_STRUCTURE_RULING_PATH)
    authorization_ruling_matches_commit = False
    if (
        AUTH_STRUCTURE_RULING_PATH.is_file()
        and not AUTH_STRUCTURE_RULING_PATH.is_symlink()
    ):
        try:
            authorization_ruling_matches_commit = (
                git_bytes(
                    "show",
                    f"{AUTH_STRUCTURE_RULING_COMMIT}:{authorization_ruling_relative}",
                )
                == AUTH_STRUCTURE_RULING_PATH.read_bytes()
            )
        except P0PerformanceError:
            authorization_ruling_matches_commit = False
    gates["authorization_structure_ruling_blob"] = authorization_ruling_matches_commit
    failed = [name for name, passed in gates.items() if not passed]
    if failed:
        raise P0PerformanceError(
            "P0-P frozen evidence identity failed: " + ", ".join(failed)
        )
    return {
        "gate_pass": True,
        "gates": gates,
        "p0_d_evidence": evidence,
        "pro_ruling_commit": PRO_RULING_COMMIT,
        "pro_ruling_path": ruling_relative,
        "authorization_structure_ruling_commit": AUTH_STRUCTURE_RULING_COMMIT,
        "authorization_structure_ruling_path": authorization_ruling_relative,
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
    return effective, {
        "benchmark": benchmark,
        "source_case": definition["source_case"],
        "source_path": relative(source_path),
        "source_file_sha256": definition["source_config_sha256"],
        "source_canonical_sha256": canonical_sha256(baseline),
        "effective_canonical_sha256": canonical_sha256(effective),
        "differences": differences,
    }


def sample_identity(sample_id: str) -> dict[str, str]:
    if sample_id not in EXPANDED_EXECUTION_SCHEDULE:
        raise P0PerformanceError(f"sample is outside the frozen schedule: {sample_id}")
    if sample_id.startswith("WARMUP_"):
        disposition = "warmup"
        benchmark = sample_id.removeprefix("WARMUP_")
        mode = "unprofiled"
    elif sample_id.startswith("PROFILED_"):
        disposition = "profiled"
        parts = sample_id.split("_")
        if len(parts) != 3:
            raise P0PerformanceError(f"expanded profile sample is invalid: {sample_id}")
        benchmark = parts[1]
        mode = parts[2].lower()
    else:
        disposition = "timed"
        benchmark = sample_id.rsplit("_", maxsplit=1)[-1]
        mode = "unprofiled"
    if benchmark not in EXPECTED_BENCHMARKS:
        raise P0PerformanceError(f"sample benchmark is invalid: {sample_id}")
    if mode not in SAMPLE_MODES:
        raise P0PerformanceError(f"sample instrumentation mode is invalid: {sample_id}")
    return {
        "sample_id": sample_id,
        "benchmark": benchmark,
        "disposition": disposition,
        "mode": mode,
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

    from p0p_authorization import load_authorization as load_parent_ticket

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
    "AUTH_STRUCTURE_RULING_COMMIT",
    "AUTH_STRUCTURE_RULING_PATH",
    "CONTRACT_COMMIT",
    "CONTRACT_ID",
    "CONTRACT_PATH",
    "EXPANDED_EXECUTION_SCHEDULE",
    "EXPECTED_BENCHMARKS",
    "EXPECTED_GPU_PYTHON_VERSIONS",
    "EXPECTED_SCHEDULE",
    "FORMAL_ROOT",
    "FORMAL_SOURCE",
    "FORMAL_SOURCE_SHA256",
    "GPU_PYTHON",
    "NOT_AUTHORIZED_STATUS",
    "NSYS_PATH",
    "P0_D_EVIDENCE_HASHES",
    "PROFILE_BUNDLE",
    "PRO_RULING_COMMIT",
    "PRO_RULING_PATH",
    "R1_PREREGISTRATION_PATH",
    "RAW_ROOT",
    "REGISTERED_TIMES_S",
    "SAMPLE_MODES",
    "STATIC_ROOT",
    "P0PerformanceError",
    "assert_no_sample_output_root",
    "atomic_json",
    "atomic_text",
    "canonical_json_bytes",
    "canonical_sha256",
    "display_path",
    "effective_config",
    "evidence_identity",
    "execution_binding",
    "formal_source_identity",
    "frozen_child_environment",
    "git_text",
    "load_authorization",
    "load_contract",
    "numerical_quality_gates",
    "relative",
    "sample_identity",
    "sha256_file",
    "strict_json",
    "strict_json_bytes",
    "validate_schedule_prefix",
]
