#!/usr/bin/env python3
"""Result-blind P0-D audit of frozen D3R1 accepted-step diagnostics.

Only the exact series frozen in ``00_P0性能与散度诊断预注册.json`` are loaded.
Complete archives are addressed through the leading ``arrays.fields`` metadata
object in ``result.json``; the failed partial is addressed through an explicit
logical-name mapping.  State, grid-array, observable, and D3 classification
values are never loaded.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import struct
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, TypeAlias

import numpy as np
from numpy.typing import NDArray

HERE = Path(__file__).resolve().parent
REPOSITORY_ROOT = HERE.parents[2]
D3_ROOT = HERE.parent / "07_D3跨网格时间步响应诊断"
RAW_ROOT = D3_ROOT / "artifacts" / "r1" / "runs"
CONTRACT_PATH = HERE / "00_P0性能与散度诊断预注册.json"
R1_PREREGISTRATION_PATH = D3_ROOT / "d3_cross_grid_temporal_preregistration_r1.json"
OUTPUT_ROOT = HERE / "artifacts" / "p0"
OUTPUT_JSON = OUTPUT_ROOT / "divergence_diagnostics.json"
OUTPUT_MD = OUTPUT_ROOT / "divergence_diagnostics.md"
ACCESS_LEDGER_PATH = OUTPUT_ROOT / "divergence_access_ledger.json"

COMPLETE_CASES = ("G6_DT0", "G6_DT1", "G6_DT2", "G9_DT0", "G9_DT1")
ALL_CASES = (*COMPLETE_CASES, "G9_DT2")
FAILED_CASE = "G9_DT2"

EXPECTED_CONTRACT_COMMIT = "246a4d5c365b022ad4bd2e930393bacdd59fef61"
EXPECTED_RAW_SNAPSHOT = (
    "906a32f71f3e095afc7cba6a3eddf7a83e4d639d36dc0b3014278b3e0001b032"
)
EXPECTED_RAW_FILE_COUNT = 40
EXPECTED_RAW_TOTAL_BYTES = 359_454_291
EXPECTED_FORMAL_SOURCE_TREE_SHA256 = (
    "dcd27cbf4353ab8b0d53802c4810f4423d3a34a45ea2944bdd8346e4eab54bb8"
)
EXPECTED_R1_PREREGISTRATION_SHA256 = (
    "bc6beda32e942292f5e17bd8236b9f99a2e1966c07ac2b0d620c39612d0a4354"
)
EXPECTED_R1_RUNNER_SHA256 = (
    "d5103e4b26a066bc7c3aeccc14990ab28c3a82d21619eab6fc817bbb60e2b1b0"
)
EXPECTED_FAILED_PARTIAL_SHA256 = (
    "8f1e0973eda48c2ecc54f10001f28b988d353e85a728a16ec24f74582527cbcc"
)
EXPECTED_FAILED_PARTIAL_SIZE = 60_848_588
R1_RUNNER_PATH = D3_ROOT / "run_d3_cross_grid_temporal.py"
FAILED_PARTIAL_PATH = RAW_ROOT / "cases" / FAILED_CASE / "partial.npz"
FORMAL_PROGRAM_ROOT = REPOSITORY_ROOT / "02_模拟程序"
SOURCE_TREE_HASH_DOMAIN = b"airvortex-source-tree-v1\0"


class P0DiagnosticError(RuntimeError):
    """Fail-closed P0-D contract or evidence error."""


FloatArray: TypeAlias = NDArray[np.float64]


def _relative(path: Path) -> str:
    return path.resolve().relative_to(REPOSITORY_ROOT.resolve()).as_posix()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_text(*arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(REPOSITORY_ROOT), *arguments],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise P0DiagnosticError(f"Git precondition failed for {arguments!r}: {detail}")
    return completed.stdout.rstrip("\n")


def _git_bytes(*arguments: str) -> bytes:
    completed = subprocess.run(
        ["git", "-C", str(REPOSITORY_ROOT), *arguments],
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise P0DiagnosticError(f"Git precondition failed for {arguments!r}: {detail}")
    return completed.stdout


def _git_is_ancestor(ancestor: str, descendant: str) -> bool:
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
        raise P0DiagnosticError(f"Git ancestry check failed: {detail}")
    return completed.returncode == 0


def _controlled_source_tree_identity() -> dict[str, Any]:
    """Independently reproduce the frozen formal source-tree identity."""

    root = FORMAL_PROGRAM_ROOT.resolve()
    if not root.is_dir() or root.is_symlink():
        raise P0DiagnosticError("formal program root is not a regular directory")
    candidates: list[Path] = []
    for relative in ("pyproject.toml", "run.py", "run_gui.sh"):
        candidate = root / relative
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
        raise P0DiagnosticError("formal source-tree allow-list is empty")
    digest = hashlib.sha256()
    digest.update(SOURCE_TREE_HASH_DOMAIN)
    total_bytes = 0
    relative_paths: list[str] = []
    for path in files:
        relative_text = path.relative_to(root).as_posix()
        relative_bytes = relative_text.encode("utf-8")
        payload = path.read_bytes()
        digest.update(len(relative_bytes).to_bytes(8, "big"))
        digest.update(relative_bytes)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
        relative_paths.append(relative_text)
        total_bytes += len(payload)
    return {
        "algorithm": "sha256",
        "sha256": digest.hexdigest(),
        "file_count": len(files),
        "total_bytes": total_bytes,
        "relative_paths": relative_paths,
    }


def _execution_binding(contract: Mapping[str, Any]) -> dict[str, Any]:
    """Fail before numeric access unless code, contract, Git, and raw bind exactly."""

    branch = _git_text("branch", "--show-current")
    head = _git_text("rev-parse", "HEAD")
    origin_main = _git_text("rev-parse", "origin/main")
    status = _git_text("status", "--porcelain=v1", "--untracked-files=all")
    if branch != "main":
        raise P0DiagnosticError(f"P0-D execution requires main, found {branch!r}")
    if status:
        raise P0DiagnosticError("P0-D execution requires a completely clean worktree")
    if head != origin_main:
        raise P0DiagnosticError(
            "local main and origin/main differ before P0-D execution"
        )
    if not _git_is_ancestor(EXPECTED_CONTRACT_COMMIT, head):
        raise P0DiagnosticError("frozen P0 contract commit is not an ancestor of HEAD")

    script_path = Path(__file__).resolve()
    script_relative = _relative(script_path)
    _git_text("ls-files", "--error-unmatch", "--", script_relative)
    if _git_bytes("show", f"HEAD:{script_relative}") != script_path.read_bytes():
        raise P0DiagnosticError("executing audit script differs from the HEAD blob")
    contract_relative = _relative(CONTRACT_PATH)
    if (
        _git_bytes("show", f"{EXPECTED_CONTRACT_COMMIT}:{contract_relative}")
        != CONTRACT_PATH.read_bytes()
    ):
        raise P0DiagnosticError("P0 contract differs from its frozen commit blob")

    binding = contract.get("binding")
    if not isinstance(binding, Mapping):
        raise P0DiagnosticError("P0 contract binding object is missing")
    required_ancestors = {
        "pro_authorization_commit": binding.get("pro_authorization_commit"),
        "reviewed_codex_commit": binding.get("reviewed_codex_commit"),
        "d3r1_execution_commit": binding.get("d3r1_execution_commit"),
        "previous_performance_ruling_commit": binding.get(
            "previous_performance_ruling_commit"
        ),
    }
    for label, commit in required_ancestors.items():
        if not isinstance(commit, str) or not _git_is_ancestor(commit, head):
            raise P0DiagnosticError(f"required {label} is not an ancestor of HEAD")

    formal_identity = _controlled_source_tree_identity()
    if not (
        formal_identity["sha256"] == EXPECTED_FORMAL_SOURCE_TREE_SHA256
        and binding.get("formal_source_tree_sha256")
        == EXPECTED_FORMAL_SOURCE_TREE_SHA256
        and _sha256(R1_PREREGISTRATION_PATH) == EXPECTED_R1_PREREGISTRATION_SHA256
        and binding.get("r1_preregistration_sha256")
        == EXPECTED_R1_PREREGISTRATION_SHA256
        and _sha256(R1_RUNNER_PATH) == EXPECTED_R1_RUNNER_SHA256
        and binding.get("r1_runner_sha256") == EXPECTED_R1_RUNNER_SHA256
        and FAILED_PARTIAL_PATH.stat().st_size == EXPECTED_FAILED_PARTIAL_SIZE
        and binding.get("g9_dt2_partial_size_bytes") == EXPECTED_FAILED_PARTIAL_SIZE
        and _sha256(FAILED_PARTIAL_PATH) == EXPECTED_FAILED_PARTIAL_SHA256
        and binding.get("g9_dt2_partial_sha256") == EXPECTED_FAILED_PARTIAL_SHA256
    ):
        raise P0DiagnosticError(
            "formal source, R1 protocol, runner, or partial drifted"
        )
    return {
        "branch": branch,
        "execution_commit": head,
        "origin_main": origin_main,
        "worktree_clean_before_numeric_access": True,
        "contract_commit": EXPECTED_CONTRACT_COMMIT,
        "contract_blob_matches_frozen_commit": True,
        "audit_script_tracked_and_matches_head": True,
        "required_commits_are_ancestors": True,
        "formal_source_tree": formal_identity,
        "r1_preregistration_sha256": EXPECTED_R1_PREREGISTRATION_SHA256,
        "r1_runner_sha256": EXPECTED_R1_RUNNER_SHA256,
        "g9_dt2_partial_size_bytes": EXPECTED_FAILED_PARTIAL_SIZE,
        "g9_dt2_partial_sha256": EXPECTED_FAILED_PARTIAL_SHA256,
    }


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant is prohibited: {value}")


def _no_duplicate_object(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _strict_json(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise P0DiagnosticError(f"not a regular JSON file: {_relative(path)}")
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_no_duplicate_object,
            parse_constant=_reject_constant,
        )
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise P0DiagnosticError(
            f"strict JSON reload failed: {_relative(path)}"
        ) from exc
    if not isinstance(value, dict):
        raise P0DiagnosticError(f"top-level JSON object required: {_relative(path)}")
    return value


def _raw_snapshot() -> dict[str, Any]:
    if not RAW_ROOT.is_dir() or RAW_ROOT.is_symlink():
        raise P0DiagnosticError("frozen R1 raw root is unavailable")
    symlinks = sorted(
        path.relative_to(RAW_ROOT).as_posix()
        for path in RAW_ROOT.rglob("*")
        if path.is_symlink()
    )
    if symlinks:
        raise P0DiagnosticError(f"frozen R1 raw contains symlinks: {symlinks}")
    files: list[dict[str, Any]] = []
    for path in sorted(item for item in RAW_ROOT.rglob("*") if item.is_file()):
        files.append(
            {
                "path": path.relative_to(RAW_ROOT).as_posix(),
                "size_bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
        )
    canonical = json.dumps(
        files, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return {
        "file_count": len(files),
        "total_size_bytes": sum(int(item["size_bytes"]) for item in files),
        "canonical_snapshot_sha256": hashlib.sha256(canonical).hexdigest(),
        "files": files,
    }


def _require_snapshot(snapshot: Mapping[str, Any], label: str) -> None:
    if not (
        snapshot.get("file_count") == EXPECTED_RAW_FILE_COUNT
        and snapshot.get("total_size_bytes") == EXPECTED_RAW_TOTAL_BYTES
        and snapshot.get("canonical_snapshot_sha256") == EXPECTED_RAW_SNAPSHOT
    ):
        raise P0DiagnosticError(f"frozen R1 raw identity drifted at {label}")


def _extract_fields_metadata(path: Path) -> dict[str, Any]:
    """Parse only the leading arrays.fields object from an archive result JSON."""

    if not path.is_file() or path.is_symlink():
        raise P0DiagnosticError(
            f"archive result metadata is not regular: {_relative(path)}"
        )
    marker = '"fields"'
    prefix = ""
    with path.open("r", encoding="utf-8", newline="") as handle:
        while marker not in prefix:
            char = handle.read(1)
            if not char:
                raise P0DiagnosticError("arrays.fields metadata marker is missing")
            prefix += char
            if len(prefix) > 65_536:
                raise P0DiagnosticError(
                    "arrays.fields metadata marker is unexpectedly late"
                )
        if '"arrays"' not in prefix or '"observables"' in prefix:
            raise P0DiagnosticError(
                "arrays.fields metadata is not the leading field map"
            )
        while True:
            char = handle.read(1)
            if not char:
                raise P0DiagnosticError("arrays.fields object did not begin")
            if char == "{":
                break
            if not (char.isspace() or char == ":"):
                raise P0DiagnosticError("malformed arrays.fields metadata prefix")
        captured = ["{"]
        depth = 1
        in_string = False
        escaped = False
        while depth:
            char = handle.read(1)
            if not char:
                raise P0DiagnosticError("unterminated arrays.fields metadata object")
            captured.append(char)
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
            if len(captured) > 262_144:
                raise P0DiagnosticError("arrays.fields metadata object is too large")
    try:
        value = json.loads(
            "".join(captured),
            object_pairs_hook=_no_duplicate_object,
            parse_constant=_reject_constant,
        )
    except (ValueError, json.JSONDecodeError) as exc:
        raise P0DiagnosticError("arrays.fields metadata is not strict JSON") from exc
    if not isinstance(value, dict):
        raise P0DiagnosticError("arrays.fields metadata must be an object")
    return value


def _series_sha256(array: FloatArray) -> str:
    contiguous = np.ascontiguousarray(array)
    digest = hashlib.sha256()
    digest.update(contiguous.dtype.str.encode("ascii"))
    digest.update(b"\0")
    for dimension in contiguous.shape:
        digest.update(struct.pack("<Q", int(dimension)))
    digest.update(contiguous.view(np.uint8).tobytes())
    return digest.hexdigest()


def _validate_series(name: str, value: NDArray[Any]) -> FloatArray:
    array = np.asarray(value)
    if array.dtype != np.dtype(np.float64):
        raise P0DiagnosticError(f"{name} dtype is not float64: {array.dtype}")
    if array.ndim != 1:
        raise P0DiagnosticError(f"{name} is not rank one: {array.shape}")
    if not np.isfinite(array).all():
        raise P0DiagnosticError(f"{name} contains a non-finite value")
    return np.ascontiguousarray(array).copy()


def _load_completed_case(
    case_id: str,
    allowed: Sequence[str],
    access: dict[str, Any],
) -> tuple[dict[str, FloatArray], dict[str, Any]]:
    root = RAW_ROOT / "cases" / case_id
    summary = _strict_json(root / "case_summary.json")
    if not (
        summary.get("case_id") == case_id
        and summary.get("status") == "D3_CASE_COMPLETE_ALL_HARD_GATES_PASS"
        and summary.get("gate_pass") is True
    ):
        raise P0DiagnosticError(f"completed case summary rejected: {case_id}")
    result_path = root / "archive" / "result.json"
    arrays_path = root / "archive" / "arrays.npz"
    fields = _extract_fields_metadata(result_path)
    member_map: dict[str, str] = {}
    for logical in allowed:
        metadata = fields.get(logical)
        if not isinstance(metadata, Mapping):
            raise P0DiagnosticError(
                f"{case_id} missing allowed logical field: {logical}"
            )
        if metadata.get("dtype") != "float64":
            raise P0DiagnosticError(f"{case_id} logical field dtype drifted: {logical}")
        member = metadata.get("npz_key")
        if not isinstance(member, str) or not member.startswith("field_"):
            raise P0DiagnosticError(
                f"{case_id} logical field member is invalid: {logical}"
            )
        member_map[logical] = member
    if len(set(member_map.values())) != len(member_map):
        raise P0DiagnosticError(f"{case_id} allowed fields do not map one-to-one")
    loaded: dict[str, FloatArray] = {}
    with np.load(arrays_path, allow_pickle=False) as archive:
        members = sorted(archive.files)
        missing = sorted(set(member_map.values()) - set(members))
        if missing:
            raise P0DiagnosticError(f"{case_id} mapped members are missing: {missing}")
        for logical, member in member_map.items():
            loaded[logical] = _validate_series(logical, archive[member])
    access[case_id] = {
        "artifact": _relative(arrays_path),
        "artifact_sha256": _sha256(arrays_path),
        "enumerated_members": members,
        "enumerated_logical_field_names": sorted(fields),
        "loaded_logical_to_member": member_map,
        "loaded_member_names": sorted(member_map.values()),
        "prohibited_member_values_loaded": False,
    }
    return loaded, summary


def _load_failed_case(
    mapping: Mapping[str, str],
    access: dict[str, Any],
) -> tuple[dict[str, FloatArray], dict[str, Any]]:
    root = RAW_ROOT / "cases" / FAILED_CASE
    summary = _strict_json(root / "case_summary.json")
    failure = summary.get("failure")
    if not (
        summary.get("case_id") == FAILED_CASE
        and summary.get("status") == "D3_INCONCLUSIVE_FAIL_CLOSED"
        and isinstance(failure, Mapping)
        and failure.get("message") == "G9_DT2 hard gate failed: projected_divergence"
        and failure.get("accepted_steps") == 6695
    ):
        raise P0DiagnosticError("failed case summary identity was rejected")
    arrays_path = root / "partial.npz"
    loaded: dict[str, FloatArray] = {}
    with np.load(arrays_path, allow_pickle=False) as archive:
        members = sorted(archive.files)
        missing = sorted(set(mapping.values()) - set(members))
        if missing:
            raise P0DiagnosticError(
                f"failed partial allowed members are missing: {missing}"
            )
        for logical, member in mapping.items():
            loaded[logical] = _validate_series(logical, archive[member])
    access[FAILED_CASE] = {
        "artifact": _relative(arrays_path),
        "artifact_sha256": _sha256(arrays_path),
        "enumerated_members": members,
        "loaded_logical_to_member": dict(mapping),
        "loaded_member_names": sorted(mapping.values()),
        "prohibited_member_values_loaded": False,
    }
    return loaded, summary


def _uint64(value: float | np.float64) -> int:
    return struct.unpack("<Q", struct.pack("<d", float(value)))[0]


def _nullable_float(value: float | np.floating[Any]) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise P0DiagnosticError("non-finite statistic would enter the report")
    return result


def _window_statistics(time_s: FloatArray, values: FloatArray) -> dict[str, Any]:
    if not values.size:
        return {
            "count": 0,
            "minimum": None,
            "maximum": None,
            "arithmetic_mean": None,
            "population_standard_deviation": None,
            "median": None,
            "p90_linear": None,
            "p95_linear": None,
            "p99_linear": None,
            "first_value": None,
            "last_value": None,
            "ordinary_least_squares_slope_per_second": None,
        }
    centred = time_s - np.mean(time_s)
    denominator = float(np.dot(centred, centred))
    slope = (
        float(np.dot(centred, values - np.mean(values)) / denominator)
        if denominator > 0.0
        else 0.0
    )
    return {
        "count": int(values.size),
        "minimum": _nullable_float(np.min(values)),
        "maximum": _nullable_float(np.max(values)),
        "arithmetic_mean": _nullable_float(np.mean(values)),
        "population_standard_deviation": _nullable_float(np.std(values, ddof=0)),
        "median": _nullable_float(np.median(values)),
        "p90_linear": _nullable_float(np.quantile(values, 0.90, method="linear")),
        "p95_linear": _nullable_float(np.quantile(values, 0.95, method="linear")),
        "p99_linear": _nullable_float(np.quantile(values, 0.99, method="linear")),
        "first_value": _nullable_float(values[0]),
        "last_value": _nullable_float(values[-1]),
        "ordinary_least_squares_slope_per_second": _nullable_float(slope),
    }


def _row(series: Mapping[str, FloatArray], index: int) -> dict[str, Any]:
    return {
        "accepted_step": index + 1,
        "time_s": _nullable_float(series["diagnostic_step_time_s"][index]),
        "time_uint64": _uint64(series["diagnostic_step_time_s"][index]),
        "dt_s": _nullable_float(series["diagnostic_dt_s"][index]),
        "limiter_code": int(series["diagnostic_time_step_limiter_code"][index]),
        "retry_count": int(series["diagnostic_cfl_retry_count"][index]),
        "pressure_relative_residual": _nullable_float(
            series["diagnostic_pressure_relative_residual"][index]
        ),
        "pressure_iterations": int(series["diagnostic_pressure_iterations"][index]),
        "pre_projection_cfl": _nullable_float(
            series["diagnostic_pre_projection_advective_cfl"][index]
        ),
        "post_projection_cfl": _nullable_float(
            series["diagnostic_post_projection_advective_cfl"][index]
        ),
        "projected_divergence_s_inv": _nullable_float(
            series["diagnostic_projected_divergence_s_inv"][index]
        ),
    }


def _robust_outlier(
    value: float, history: FloatArray, *, floor: float
) -> dict[str, Any]:
    if history.size < 32:
        return {"available": False, "flag": None, "history_count": int(history.size)}
    median = float(np.median(history))
    mad = float(np.median(np.abs(history - median)))
    threshold = median + max(6.0 * mad, floor)
    return {
        "available": True,
        "flag": bool(value > threshold),
        "history_count": int(history.size),
        "history_median": median,
        "history_mad": mad,
        "threshold": threshold,
        "value": value,
    }


def _analyse_case(
    case_id: str,
    series: Mapping[str, FloatArray],
    summary: Mapping[str, Any],
    contract: Mapping[str, Any],
) -> dict[str, Any]:
    allowed = tuple(contract["p0_d"]["allowed_logical_series"])
    if set(series) != set(allowed):
        raise P0DiagnosticError(f"{case_id} loaded series do not match the allow-list")
    step_names = tuple(name for name in allowed if name != "frame_time_s")
    lengths = {name: int(series[name].size) for name in step_names}
    if len(set(lengths.values())) != 1:
        raise P0DiagnosticError(f"{case_id} accepted-step series shapes differ")
    accepted_steps = next(iter(lengths.values()))
    summary_steps = (
        summary.get("failure", {}).get("accepted_steps")
        if case_id == FAILED_CASE
        else summary.get("quality", {}).get("metrics", {}).get("accepted_steps")
    )
    if accepted_steps != summary_steps:
        raise P0DiagnosticError(f"{case_id} accepted-step count differs from summary")
    time_s = series["diagnostic_step_time_s"]
    dt_s = series["diagnostic_dt_s"]
    if not (
        accepted_steps > 0
        and np.all(np.diff(time_s) > 0.0)
        and np.all(dt_s > 0.0)
        and np.all(
            np.equal(
                series["diagnostic_time_step_limiter_code"],
                np.floor(series["diagnostic_time_step_limiter_code"]),
            )
        )
        and np.all(
            np.equal(
                series["diagnostic_cfl_retry_count"],
                np.floor(series["diagnostic_cfl_retry_count"]),
            )
        )
        and np.all(
            np.equal(
                series["diagnostic_pressure_iterations"],
                np.floor(series["diagnostic_pressure_iterations"]),
            )
        )
    ):
        raise P0DiagnosticError(
            f"{case_id} accepted-step identity or integer series failed"
        )
    divergence = series["diagnostic_projected_divergence_s_inv"]
    limit = float(contract["p0_d"]["frozen_limits"]["projected_divergence_max_s_inv"])
    max_index = int(np.argmax(divergence))
    threshold_events = {}
    for ratio in contract["p0_d"]["threshold_ratios"]:
        indices = np.flatnonzero(divergence >= float(ratio) * limit)
        threshold_events[f"{float(ratio):.2f}"] = {
            "threshold_s_inv": float(ratio) * limit,
            "count": int(indices.size),
            "first": _row(series, int(indices[0])) if indices.size else None,
            "last": _row(series, int(indices[-1])) if indices.size else None,
        }
    windows = {}
    terminal = float(time_s[-1])
    for definition in contract["p0_d"]["fixed_windows"]:
        lower = float(definition["lower_s"])
        upper = terminal if definition.get("upper") else float(definition["upper_s"])
        lower_mask = (
            time_s >= lower if definition["lower_inclusive"] else time_s > lower
        )
        upper_mask = (
            time_s <= upper if definition["upper_inclusive"] else time_s < upper
        )
        mask = lower_mask & upper_mask
        windows[str(definition["id"])] = {
            "definition": dict(definition),
            "effective_upper_s": upper,
            "projected_divergence": _window_statistics(time_s[mask], divergence[mask]),
            "pressure_relative_residual": _window_statistics(
                time_s[mask], series["diagnostic_pressure_relative_residual"][mask]
            ),
            "pressure_iterations": _window_statistics(
                time_s[mask], series["diagnostic_pressure_iterations"][mask]
            ),
            "post_projection_cfl": _window_statistics(
                time_s[mask], series["diagnostic_post_projection_advective_cfl"][mask]
            ),
        }
    frame_time = series["frame_time_s"]
    registered = np.asarray(
        contract["p0_d"]["registered_output_times_s"], dtype=np.float64
    )
    expected_frame = registered[: frame_time.size]
    if not np.array_equal(frame_time, expected_frame):
        raise P0DiagnosticError(
            f"{case_id} frame time is not the frozen registered prefix"
        )
    native_rows = []
    time_bits: dict[int, int] = {
        _uint64(value): index for index, value in enumerate(time_s)
    }
    for value in registered:
        bits = _uint64(value)
        native_rows.append(
            {
                "registered_time_s": float(value),
                "registered_time_uint64": bits,
                "accepted_step_row": _row(series, time_bits[bits])
                if bits in time_bits
                else None,
            }
        )
    identities = {
        name: {
            "dtype": series[name].dtype.str,
            "shape": list(series[name].shape),
            "sha256": _series_sha256(series[name]),
        }
        for name in allowed
    }
    maximum = float(divergence[max_index])
    result: dict[str, Any] = {
        "case_id": case_id,
        "accepted_steps": accepted_steps,
        "terminal_time_s": terminal,
        "frame_count": int(frame_time.size),
        "series_identity": identities,
        "maximum_projected_divergence_s_inv": maximum,
        "maximum_divergence_step": max_index + 1,
        "maximum_divergence_time_s": float(time_s[max_index]),
        "maximum_to_gate_ratio": maximum / limit,
        "maximum_safe_margin_fraction": 1.0 - maximum / limit,
        "threshold_events": threshold_events,
        "fixed_windows": windows,
        "native_output_diagnostics": native_rows,
    }
    if case_id == FAILED_CASE:
        terminal_indices = list(range(max(0, accepted_steps - 64), accepted_steps))
        ranked = sorted(
            range(accepted_steps), key=lambda i: (-float(divergence[i]), i)
        )[:32]
        last = accepted_steps - 1
        previous = slice(max(0, last - 512), last)
        pressure = float(series["diagnostic_pressure_relative_residual"][last])
        iterations = float(series["diagnostic_pressure_iterations"][last])
        post_cfl = float(series["diagnostic_post_projection_advective_cfl"][last])
        result["failure_focus"] = {
            "last_64_rows": [_row(series, index) for index in terminal_indices],
            "highest_32_divergence_rows": [_row(series, index) for index in ranked],
            "failure_step": _row(series, last),
            "failure_step_is_series_maximum": last == max_index,
            "failure_step_is_registered_output_alignment": (
                _uint64(time_s[last]) in {_uint64(value) for value in registered}
                or int(series["diagnostic_time_step_limiter_code"][last]) == 5
            ),
            "pressure_residual_to_gate_ratio": pressure
            / float(
                contract["p0_d"]["frozen_limits"]["pressure_relative_residual_max"]
            ),
            "pressure_residual_near_gate": pressure
            / float(contract["p0_d"]["frozen_limits"]["pressure_relative_residual_max"])
            >= 0.8,
            "post_projection_cfl_to_gate_ratio": post_cfl
            / float(contract["p0_d"]["frozen_limits"]["accepted_advective_cfl_max"]),
            "post_projection_cfl_near_gate": post_cfl
            / float(contract["p0_d"]["frozen_limits"]["accepted_advective_cfl_max"])
            >= 0.8,
            "retry_present": bool(series["diagnostic_cfl_retry_count"][last] > 0.0),
            "pressure_iteration_robust_outlier": _robust_outlier(
                iterations,
                series["diagnostic_pressure_iterations"][previous],
                floor=1.0,
            ),
            "divergence_robust_outlier": _robust_outlier(
                float(divergence[last]),
                divergence[previous],
                floor=64.0
                * np.finfo(np.float64).eps
                * max(abs(float(np.median(divergence[previous]))), 1.0e-4),
            ),
        }
    return result


def _native_cross_case(cases: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for grid in ("G6", "G9"):
        for position in range(11):
            rows = {}
            for level in ("DT0", "DT1", "DT2"):
                case_id = f"{grid}_{level}"
                case = cases[case_id]
                entry = case["native_output_diagnostics"][position]
                rows[level] = entry["accepted_step_row"]
            result.append(
                {
                    "grid": grid,
                    "registered_time_s": position * 0.001,
                    "DT0": rows["DT0"],
                    "DT1": rows["DT1"],
                    "DT2": rows["DT2"],
                }
            )
    return result


def _markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# HF3D P0-D 结果盲散度诊断",
        "",
        "状态：`P0_D_RESULT_BLIND_DIVERGENCE_DIAGNOSTICS_PASS`",
        "",
        "本报告只描述冻结的逐接受步数值诊断。没有读取状态场、网格数组值、物理观测量或 D3 分类向量，不能形成 D3 科学分类、数值资格、物理验证或装置预测。",
        "",
        "## 身份与访问边界",
        "",
        f"- 预注册提交：`{report['contract_commit']}`",
        f"- R1 执行提交：`{report['d3r1_execution_commit']}`",
        f"- 正式源码树：`{report['formal_source_tree_sha256']}`",
        f"- raw 文件：`{report['raw_snapshot_before']['file_count']}` 个，`{report['raw_snapshot_before']['total_size_bytes']}` 字节",
        f"- raw 规范 SHA-256：`{report['raw_snapshot_before']['canonical_snapshot_sha256']}`",
        "- `physical_observables_read = false`；`prohibited_numeric_values_loaded = false`。",
        "",
        "## 六案例散度上界",
        "",
        "| 案例 | 接受步 | 终止时刻 s | 最大散度 s^-1 | 门比 | 最大步 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for case_id in ALL_CASES:
        case = report["cases"][case_id]
        lines.append(
            f"| {case_id} | {case['accepted_steps']} | {case['terminal_time_s']:.12g} | "
            f"{case['maximum_projected_divergence_s_inv']:.12g} | "
            f"{case['maximum_to_gate_ratio']:.8f} | {case['maximum_divergence_step']} |"
        )
    lines.extend(
        [
            "",
            "## 冻结门比例计数",
            "",
            "| 案例 | >= 0.80 | >= 0.90 | >= 0.95 | >= 1.00 |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for case_id in ALL_CASES:
        events = report["cases"][case_id]["threshold_events"]
        lines.append(
            f"| {case_id} | {events['0.80']['count']} | {events['0.90']['count']} | "
            f"{events['0.95']['count']} | {events['1.00']['count']} |"
        )
    focus = report["cases"][FAILED_CASE]["failure_focus"]
    lines.extend(
        [
            "",
            "## G9_DT2 失败步描述",
            "",
            f"- 失败步是本序列最大散度步：`{str(focus['failure_step_is_series_maximum']).lower()}`",
            f"- 失败步是登记输出对齐步：`{str(focus['failure_step_is_registered_output_alignment']).lower()}`",
            f"- 压力残差/冻结门：`{focus['pressure_residual_to_gate_ratio']:.12g}`；接近门：`{str(focus['pressure_residual_near_gate']).lower()}`",
            f"- 投影后 CFL/冻结门：`{focus['post_projection_cfl_to_gate_ratio']:.12g}`；接近门：`{str(focus['post_projection_cfl_near_gate']).lower()}`",
            f"- 失败步存在 retry：`{str(focus['retry_present']).lower()}`",
            f"- 压力迭代稳健异常标志：`{str(focus['pressure_iteration_robust_outlier']['flag']).lower()}`",
            f"- 散度稳健异常标志：`{str(focus['divergence_robust_outlier']['flag']).lower()}`",
            "",
            "最后 64 步、散度最高 32 步、三个固定窗口、逐序列 dtype/shape/hash 和原生输出时刻跨层对照完整保存在同目录 JSON；Markdown 不重复宽表，以避免窄页截断。",
            "",
            "## 结论边界",
            "",
            "```text",
            "D3R1_S2 = FAILED_STOPPED",
            "D3R1_DIRECT_TRIGGER = PROJECTED_DIVERGENCE",
            "D3R1_FAILURE_MECHANISM = NOT_YET_ESTABLISHED",
            "D3R1_S3 = NOT_EXECUTED",
            "D3R1_SCIENTIFIC_CLASSIFICATION = NOT_FORMED",
            "HF3D_P0_D = COMPLETED",
            "HF3D_P0_P = NOT_EXECUTED_BY_THIS_TOOL",
            "HF3D_P1_IMPLEMENTATION = NOT_AUTHORIZED",
            "NUMERICAL_QUALIFICATION = NOT_DEMONSTRATED",
            "PHYSICAL_VALIDATION = NOT_PERFORMED",
            "EXPERIMENTAL_VALIDATION = NOT_PERFORMED",
            "APPARATUS_PREDICTION = DISALLOWED",
            "```",
            "",
            "这些描述性结构不能单独区分孤立尖峰、持续逼近、压力耦合、输出对齐效应或混合机制，也不能支持放宽阈值或修改正式数值合同。",
            "",
        ]
    )
    return "\n".join(lines)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    contract = _strict_json(CONTRACT_PATH)
    execution_binding = _execution_binding(contract)
    r1 = _strict_json(R1_PREREGISTRATION_PATH)
    if not (
        contract.get("status")
        == "FROZEN_BEFORE_DIAGNOSTIC_VALUES_OR_BENCHMARK_EXECUTION"
        and contract.get("binding", {}).get("r1_raw_canonical_snapshot_sha256")
        == EXPECTED_RAW_SNAPSHOT
        and r1.get("numerical_quality_hard_gates")
        == contract.get("p0_d", {}).get("frozen_limits")
        | {
            "all_state_and_diagnostic_values_finite": True,
            "complete_to_registered_end_time": True,
            "configured_time_step_active_fraction_min": 0.95,
            "cumulative_phase_clip_relative_max": 0.001,
            "cumulative_phase_repair_relative_max": 0.001,
            "liquid_volume_balance_error_relative_max": 0.001,
        }
    ):
        raise P0DiagnosticError("P0-D contract binding failed")
    before = _raw_snapshot()
    _require_snapshot(before, "before value access")
    allowed = tuple(contract["p0_d"]["allowed_logical_series"])
    mapping = contract["p0_d"]["failed_partial_name_mapping"]
    if set(mapping) != set(allowed):
        raise P0DiagnosticError("failed partial mapping differs from the allow-list")
    access_cases: dict[str, Any] = {}
    loaded_cases: dict[str, dict[str, FloatArray]] = {}
    summaries: dict[str, dict[str, Any]] = {}
    for case_id in COMPLETE_CASES:
        loaded_cases[case_id], summaries[case_id] = _load_completed_case(
            case_id, allowed, access_cases
        )
    loaded_cases[FAILED_CASE], summaries[FAILED_CASE] = _load_failed_case(
        mapping, access_cases
    )
    case_reports = {
        case_id: _analyse_case(
            case_id, loaded_cases[case_id], summaries[case_id], contract
        )
        for case_id in ALL_CASES
    }
    after_analysis = _raw_snapshot()
    _require_snapshot(after_analysis, "after value access")
    if after_analysis != before:
        raise P0DiagnosticError("frozen R1 raw changed during P0-D value access")
    access_ledger = {
        "schema_version": 1,
        "contract_id": contract["contract_id"],
        "cases": access_cases,
        "loaded_logical_series_allowlist": list(allowed),
        "physical_observables_read": False,
        "state_fields_read": False,
        "grid_array_values_read": False,
        "prohibited_numeric_values_loaded": False,
    }
    report = {
        "schema_version": 1,
        "status": "P0_D_RESULT_BLIND_DIVERGENCE_DIAGNOSTICS_PASS",
        "gate_pass": True,
        "contract_id": contract["contract_id"],
        "contract_commit": EXPECTED_CONTRACT_COMMIT,
        "contract_path": _relative(CONTRACT_PATH),
        "contract_sha256": _sha256(CONTRACT_PATH),
        "execution_binding": execution_binding,
        "audit_execution_commit": execution_binding["execution_commit"],
        "audit_script_path": _relative(Path(__file__)),
        "audit_script_sha256": _sha256(Path(__file__)),
        "d3r1_execution_commit": contract["binding"]["d3r1_execution_commit"],
        "formal_source_tree_sha256": contract["binding"]["formal_source_tree_sha256"],
        "raw_snapshot_before": before,
        "raw_snapshot_after_analysis": after_analysis,
        "access_ledger": access_ledger,
        "cases": case_reports,
        "native_output_cross_case_comparison": _native_cross_case(case_reports),
        "interpretation": {
            "direct_trigger": "PROJECTED_DIVERGENCE",
            "failure_mechanism": "NOT_YET_ESTABLISHED",
            "descriptive_flags_are_mechanism_proof": False,
            "threshold_change_supported": False,
            "physical_observables_read": False,
        },
        "conclusion_ceiling": contract["conclusion_ceiling"],
    }
    _atomic_write(
        ACCESS_LEDGER_PATH,
        json.dumps(access_ledger, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
    )
    _atomic_write(
        OUTPUT_JSON,
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
    )
    _atomic_write(OUTPUT_MD, _markdown(report))
    after_publication = _raw_snapshot()
    _require_snapshot(after_publication, "after output publication")
    if after_publication != before:
        raise P0DiagnosticError("frozen R1 raw changed while P0-D outputs published")
    print(
        json.dumps(
            {
                "status": report["status"],
                "raw_snapshot_sha256": before["canonical_snapshot_sha256"],
                "physical_observables_read": False,
                "output_json": _relative(OUTPUT_JSON),
                "output_markdown": _relative(OUTPUT_MD),
                "access_ledger": _relative(ACCESS_LEDGER_PATH),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
