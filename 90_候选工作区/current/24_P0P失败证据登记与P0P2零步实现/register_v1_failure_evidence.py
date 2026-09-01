#!/usr/bin/env python3
"""Register the consumed P0-P V1 failure without modifying its raw tree.

The source tree is ignored runtime evidence.  Registration copies every regular
file byte-for-byte into a tracked snapshot, rejects ambiguous filesystem
objects, proves the source did not change during the copy, and independently
checks the terminal transaction semantics frozen by the Pro ruling.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import tempfile
import unicodedata
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn

HERE = Path(__file__).resolve().parent
REPOSITORY_ROOT = HERE.parents[2]
SOURCE_ROOT = (
    REPOSITORY_ROOT
    / "90_候选工作区/current/17_HF3D性能优化/artifacts/p0/performance_raw"
)
EVIDENCE_ROOT = HERE / "evidence"
SNAPSHOT_ROOT = EVIDENCE_ROOT / "v1_claim_failure_snapshot"
MANIFEST_PATH = EVIDENCE_ROOT / "v1_failure_evidence_manifest.json"
AUDIT_PATH = EVIDENCE_ROOT / "v1_failure_evidence_audit.json"

TREE_HASH_DOMAIN = b"hf3d-p0p-v1-failure-evidence-tree-v1\0"
TREE_CANONICALIZATION = (
    "SHA-256 over domain bytes hf3d-p0p-v1-failure-evidence-tree-v1\\0; "
    "then each regular file in lexicographic UTF-8 POSIX-relative-path order "
    "as uint64_be(path_byte_length), path bytes, six ASCII Git-mode bytes, "
    "uint64_be(content_byte_length), and raw content bytes"
)

EXPECTED_HASHES = {
    "execution_claim.json": (
        "ecd7cf0e9dfa4e3d609f22e205c95261cc1505a255776dc56a29b3532ad94d80"
    ),
    "schedule_ledger.json": (
        "089d91578f89d083f3552f4dfdfaac464bc32842a4f7945f13e9e00903817948"
    ),
    "execution_failure.json": (
        "749ecd452c3958e6320de4e39fe9ea04d2cf4aadd8e4488f105a1cbdc5ab5262"
    ),
    "WARMUP_B1/sample_failure.json": (
        "81145152ab792a763596cbdc4859040fee47c31cc0546f033d7805cdfa2cee1b"
    ),
    "WARMUP_B1/worker.stderr.log": (
        "944c1cdd60c47f9315d20453e27a541c07f6cf98c5f8419d2b6d79d28d21e894"
    ),
}

V1_SCHEDULE_ID = "HF3D_P0P_FIXED_18_V1"
V1_SAMPLE_IDS = (
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
    "PROFILED_B2_HOST",
    "PROFILED_B2_CUDA",
    "PROFILED_B2_MEMORY",
    "PROFILED_B3_HOST",
    "PROFILED_B3_CUDA",
    "PROFILED_B3_MEMORY",
)


class EvidenceRegistrationError(RuntimeError):
    """Raised when evidence is ambiguous, inconsistent, or mutable."""


def _fail(message: str) -> NoReturn:
    raise EvidenceRegistrationError(message)


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            _fail(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def strict_json_bytes(data: bytes, *, label: str) -> Any:
    try:
        text = data.decode("utf-8", errors="strict")
        return json.loads(text, object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvidenceRegistrationError(
            f"invalid strict JSON at {label}: {exc}"
        ) from exc


def strict_json_path(path: Path) -> Any:
    return strict_json_bytes(path.read_bytes(), label=str(path))


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def _write_exclusive(path: Path, payload: bytes, mode: int = 0o644) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, mode)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                _fail(f"short write while creating {path}")
            view = view[written:]
        os.fchmod(descriptor, mode)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _git_mode(mode: int) -> str:
    return "100755" if mode & 0o111 else "100644"


def _safe_relative_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts:
        _fail(f"unsafe evidence path: {value!r}")
    if any(part in {"", ".", ".."} for part in path.parts):
        _fail(f"unsafe evidence path component: {value!r}")
    if path.as_posix() != value:
        _fail(f"non-canonical evidence path: {value!r}")
    return path


def _open_regular_bytes(path: Path, expected: os.stat_result) -> bytes:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        opened_before = os.fstat(descriptor)
        identity_before = (
            opened_before.st_dev,
            opened_before.st_ino,
            opened_before.st_mode,
            opened_before.st_nlink,
            opened_before.st_size,
            opened_before.st_mtime_ns,
            opened_before.st_ctime_ns,
        )
        expected_identity = (
            expected.st_dev,
            expected.st_ino,
            expected.st_mode,
            expected.st_nlink,
            expected.st_size,
            expected.st_mtime_ns,
            expected.st_ctime_ns,
        )
        if identity_before != expected_identity:
            _fail(f"evidence object changed before read: {path}")
        if not stat.S_ISREG(opened_before.st_mode) or opened_before.st_nlink != 1:
            _fail(f"evidence file is not regular and singly linked: {path}")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        opened_after = os.fstat(descriptor)
        identity_after = (
            opened_after.st_dev,
            opened_after.st_ino,
            opened_after.st_mode,
            opened_after.st_nlink,
            opened_after.st_size,
            opened_after.st_mtime_ns,
            opened_after.st_ctime_ns,
        )
        if identity_after != identity_before:
            _fail(f"evidence object changed during read: {path}")
        data = b"".join(chunks)
        if len(data) != opened_after.st_size:
            _fail(f"evidence read length changed: {path}")
        return data
    finally:
        os.close(descriptor)


def scan_tree(root: Path) -> dict[str, Any]:
    """Return a deterministic, race-checked manifest of a regular-file tree."""

    try:
        root_stat = root.lstat()
    except FileNotFoundError as exc:
        raise EvidenceRegistrationError(f"evidence root is absent: {root}") from exc
    if not stat.S_ISDIR(root_stat.st_mode) or root.is_symlink():
        _fail(f"evidence root must be one real directory: {root}")

    directories: list[dict[str, Any]] = []
    material: list[tuple[dict[str, Any], bytes]] = []
    normalized_paths: set[str] = set()

    def visit(directory: Path, prefix: PurePosixPath | None = None) -> None:
        try:
            entries = sorted(
                os.scandir(directory), key=lambda item: item.name.encode("utf-8")
            )
        except (OSError, UnicodeEncodeError) as exc:
            raise EvidenceRegistrationError(
                f"cannot enumerate evidence directory {directory}: {exc}"
            ) from exc
        for item in entries:
            try:
                item.name.encode("utf-8", errors="strict")
            except UnicodeEncodeError as exc:
                raise EvidenceRegistrationError(
                    f"non-UTF-8 evidence name under {directory}"
                ) from exc
            relative = (
                PurePosixPath(item.name)
                if prefix is None
                else prefix / PurePosixPath(item.name)
            )
            relative_text = relative.as_posix()
            _safe_relative_path(relative_text)
            normalized = unicodedata.normalize("NFC", relative_text)
            if normalized in normalized_paths:
                _fail(f"duplicate normalized evidence path: {relative_text}")
            normalized_paths.add(normalized)

            path = directory / item.name
            info = item.stat(follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode):
                _fail(f"symlink is prohibited in evidence: {relative_text}")
            if stat.S_ISDIR(info.st_mode):
                directories.append(
                    {
                        "path": relative_text,
                        "posix_mode": f"{stat.S_IMODE(info.st_mode):04o}",
                    }
                )
                visit(path, relative)
                continue
            if not stat.S_ISREG(info.st_mode):
                _fail(f"special filesystem object is prohibited: {relative_text}")
            if info.st_nlink != 1:
                _fail(f"hardlinked evidence file is prohibited: {relative_text}")
            data = _open_regular_bytes(path, info)
            material.append(
                (
                    {
                        "path": relative_text,
                        "posix_mode": f"{stat.S_IMODE(info.st_mode):04o}",
                        "git_mode": _git_mode(info.st_mode),
                        "byte_length": len(data),
                        "sha256": hashlib.sha256(data).hexdigest(),
                    },
                    data,
                )
            )

    visit(root)
    material.sort(key=lambda item: item[0]["path"].encode("utf-8"))
    directories.sort(key=lambda item: item["path"].encode("utf-8"))

    digest = hashlib.sha256(TREE_HASH_DOMAIN)
    for entry, data in material:
        path_bytes = entry["path"].encode("utf-8")
        mode_bytes = entry["git_mode"].encode("ascii")
        digest.update(len(path_bytes).to_bytes(8, "big"))
        digest.update(path_bytes)
        digest.update(mode_bytes)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)

    files = [entry for entry, _data in material]
    return {
        "schema_version": 1,
        "root_posix_mode": f"{stat.S_IMODE(root_stat.st_mode):04o}",
        "directories": directories,
        "directory_count": len(directories),
        "files": files,
        "file_count": len(files),
        "total_bytes": sum(int(entry["byte_length"]) for entry in files),
        "canonicalization": TREE_CANONICALIZATION,
        "canonical_tree_sha256": digest.hexdigest(),
    }


def _file_map(tree: dict[str, Any]) -> dict[str, dict[str, Any]]:
    files = tree.get("files")
    if not isinstance(files, list):
        _fail("tree manifest files must be a list")
    result: dict[str, dict[str, Any]] = {}
    for raw in files:
        if not isinstance(raw, dict) or not isinstance(raw.get("path"), str):
            _fail("invalid tree manifest file entry")
        path = str(raw["path"])
        _safe_relative_path(path)
        if path in result:
            _fail(f"duplicate tree manifest path: {path}")
        result[path] = raw
    return result


def stable_byte_identity(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """Compare path, Git mode, length and bytes while ignoring checkout chmod."""

    left_map = _file_map(left)
    right_map = _file_map(right)
    if set(left_map) != set(right_map):
        return False
    identity_fields = ("git_mode", "byte_length", "sha256")
    return all(
        all(
            left_map[path].get(key) == right_map[path].get(key)
            for key in identity_fields
        )
        for path in left_map
    ) and left.get("canonical_tree_sha256") == right.get("canonical_tree_sha256")


def copy_regular_tree(
    source: Path, destination: Path, source_tree: dict[str, Any]
) -> None:
    if destination.exists() or destination.is_symlink():
        _fail(f"copy destination already exists: {destination}")
    destination.mkdir(mode=0o755)
    for directory in source_tree["directories"]:
        relative = _safe_relative_path(str(directory["path"]))
        target = destination.joinpath(*relative.parts)
        target.mkdir(mode=0o755)
        os.chmod(target, 0o755)
    for entry in source_tree["files"]:
        relative = _safe_relative_path(str(entry["path"]))
        source_path = source.joinpath(*relative.parts)
        source_stat = source_path.lstat()
        data = _open_regular_bytes(source_path, source_stat)
        if len(data) != entry["byte_length"]:
            _fail(f"source size drifted during copy: {relative}")
        if hashlib.sha256(data).hexdigest() != entry["sha256"]:
            _fail(f"source hash drifted during copy: {relative}")
        target = destination.joinpath(*relative.parts)
        target_mode = 0o755 if entry["git_mode"] == "100755" else 0o644
        _write_exclusive(target, data, target_mode)


def _strict_json_inventory(root: Path, tree: dict[str, Any]) -> dict[str, Any]:
    parsed: list[dict[str, Any]] = []
    for entry in tree["files"]:
        relative = str(entry["path"])
        path = root.joinpath(*PurePosixPath(relative).parts)
        if relative.endswith(".json"):
            strict_json_path(path)
            parsed.append({"path": relative, "records": 1})
        elif relative.endswith(".jsonl"):
            records = 0
            for number, line in enumerate(path.read_bytes().splitlines(), start=1):
                if not line.strip():
                    _fail(f"blank JSONL record at {relative}:{number}")
                strict_json_bytes(line, label=f"{relative}:{number}")
                records += 1
            parsed.append({"path": relative, "records": records})
    return {
        "gate_pass": True,
        "strict_machine_files": parsed,
        "strict_machine_file_count": len(parsed),
    }


def audit_v1_semantics(root: Path, tree: dict[str, Any]) -> dict[str, Any]:
    files = _file_map(tree)

    def load(relative: str) -> Any:
        if relative not in files:
            _fail(f"required V1 evidence file is absent: {relative}")
        return strict_json_path(root.joinpath(*PurePosixPath(relative).parts))

    claim = load("execution_claim.json")
    ledger = load("schedule_ledger.json")
    execution_failure = load("execution_failure.json")
    sample_failure = load("WARMUP_B1/sample_failure.json")
    effective = load("WARMUP_B1/effective_config.json")
    stderr_path = root / "WARMUP_B1/worker.stderr.log"
    stderr_lines = stderr_path.read_bytes().splitlines()
    if len(stderr_lines) != 1 or not stderr_lines[0].strip():
        _fail("worker stderr must contain exactly one strict JSON record")
    worker_failure = strict_json_bytes(
        stderr_lines[0], label="WARMUP_B1/worker.stderr.log:1"
    )
    strict_inventory = _strict_json_inventory(root, tree)

    known_hashes = {
        path: {
            "expected_sha256": expected,
            "actual_sha256": files.get(path, {}).get("sha256"),
            "gate_pass": files.get(path, {}).get("sha256") == expected,
        }
        for path, expected in EXPECTED_HASHES.items()
    }

    claim_schedule = claim.get("fixed_schedule", {})
    claim_items = claim_schedule.get("ordered_items")
    entries = ledger.get("entries")
    claim_ids = (
        [item.get("sample_id") for item in claim_items]
        if isinstance(claim_items, list)
        and all(isinstance(item, dict) for item in claim_items)
        else None
    )
    ledger_ids = (
        [entry.get("sample_id") for entry in entries]
        if isinstance(entries, list)
        and all(isinstance(entry, dict) for entry in entries)
        else None
    )
    first = entries[0] if isinstance(entries, list) and entries else {}
    remaining = entries[1:] if isinstance(entries, list) else []

    stdout_entry = files.get("WARMUP_B1/worker.stdout.jsonl", {})
    completion_names = {
        "execution_complete.json",
        "WARMUP_B1/sample_manifest.json",
        "WARMUP_B1/partial_evidence.json",
        "WARMUP_B1/archive.npz",
    }
    trace = worker_failure.get("traceback")
    trace_is_preruntime = isinstance(trace, str) and all(
        token in trace
        for token in (
            "SimulationConfig.from_dict(payload)",
            "drive ramp duration cannot exceed run duration",
        )
    )

    gates = {
        "known_five_hashes_match": all(
            item["gate_pass"] for item in known_hashes.values()
        ),
        "all_json_and_jsonl_parse_strictly": strict_inventory["gate_pass"],
        "claim_identity_and_schedule_exact": (
            claim.get("status") == "HF3D_P0P_SINGLE_SCHEDULE_CLAIMED"
            and claim_schedule.get("schedule_id") == V1_SCHEDULE_ID
            and claim_ids == list(V1_SAMPLE_IDS)
            and len(claim_ids or []) == 18
        ),
        "ledger_claim_cross_reference_exact": (
            ledger.get("claim_sha256") == EXPECTED_HASHES["execution_claim.json"]
            and ledger.get("fixed_schedule_id") == V1_SCHEDULE_ID
            and ledger_ids == list(V1_SAMPLE_IDS)
            and all(
                isinstance(entry, dict) and entry.get("sequence_index") == index
                for index, entry in enumerate(entries or [])
            )
        ),
        "warmup_b1_terminal_start_exact": (
            first.get("sample_id") == "WARMUP_B1"
            and first.get("sequence_index") == 0
            and first.get("start_count") == 1
            and first.get("state") == "FAILED_STOPPED"
        ),
        "remaining_17_permanently_not_started": (
            len(remaining) == 17
            and all(
                entry.get("state") == "NOT_STARTED"
                and entry.get("start_count") == 0
                and entry.get("started_monotonic_ns") is None
                and entry.get("completed_monotonic_ns") is None
                and entry.get("launch_token_sha256") is None
                and entry.get("manifest_sha256") is None
                and entry.get("failure") is None
                for entry in remaining
            )
        ),
        "ledger_and_failures_terminal_no_resume": (
            ledger.get("status") == "FAILED_STOPPED"
            and execution_failure.get("status")
            == "HF3D_P0P_CLAIM_CONSUMED_FAILED_STOPPED_NO_RESUME"
            and execution_failure.get("active_sample") == "WARMUP_B1"
            and execution_failure.get("active_sequence_index") == 0
            and execution_failure.get("completed_samples") == []
            and sample_failure.get("status")
            == "HF3D_P0P_SAMPLE_FAILED_STOPPED_NO_RETRY"
            and sample_failure.get("sample_id") == "WARMUP_B1"
            and sample_failure.get("message") == "sample worker/profiler exited 2"
            and execution_failure.get("message") == "sample worker/profiler exited 2"
        ),
        "effective_config_conflict_exact": (
            effective.get("run", {}).get("duration_s") == 0.002
            and effective.get("run", {}).get("output_interval_s") == 0.001
            and effective.get("drive", {}).get("ramp_duration_s") == 0.005
            and effective.get("drive", {}).get("actual_speed_schedule_time_s") == []
        ),
        "worker_config_error_exact_before_runtime": (
            worker_failure.get("exception_type") == "ConfigError"
            and worker_failure.get("message")
            == "drive ramp duration cannot exceed run duration"
            and worker_failure.get("status") == "HF3D_P0_SAMPLE_FAILED_STOPPED"
            and worker_failure.get("sample_id") == "WARMUP_B1"
            and trace_is_preruntime
        ),
        "partial_evidence_null": worker_failure.get("partial_evidence") is None,
        "worker_stdout_empty": stdout_entry.get("byte_length") == 0,
        "completion_and_dynamic_result_files_absent": not (
            completion_names & set(files)
        ),
        "hf3d_time_steps_advanced_zero_from_preruntime_failure": (
            trace_is_preruntime
            and worker_failure.get("partial_evidence") is None
            and stdout_entry.get("byte_length") == 0
            and not (completion_names & set(files))
        ),
    }
    return {
        "schema_version": 1,
        "status": (
            "HF3D_P0P_V1_FAILURE_EVIDENCE_SEMANTICS_PASS"
            if all(gates.values())
            else "EVIDENCE_REGISTRATION_FAILED_STOPPED"
        ),
        "gate_pass": all(gates.values()),
        "gates": gates,
        "known_hashes": known_hashes,
        "strict_json_audit": strict_inventory,
        "derived_fact": {
            "hf3d_time_steps_advanced": 0
            if gates["hf3d_time_steps_advanced_zero_from_preruntime_failure"]
            else None,
            "basis": (
                "ConfigError in SimulationConfig.from_dict before runtime setup, "
                "partial_evidence=null, empty worker stdout, and no completion, "
                "sample manifest, partial state, or archive"
            ),
            "claim_consumed": True,
            "measured_performance_result_available": False,
        },
    }


def _relative(path: Path) -> str:
    return path.relative_to(REPOSITORY_ROOT).as_posix()


def register_evidence() -> dict[str, Any]:
    if EVIDENCE_ROOT.exists() or EVIDENCE_ROOT.is_symlink():
        _fail(f"evidence output already exists; use --check: {EVIDENCE_ROOT}")

    source_before = scan_tree(SOURCE_ROOT)
    source_semantics = audit_v1_semantics(SOURCE_ROOT, source_before)
    if not source_semantics["gate_pass"]:
        _fail("source V1 semantics failed before copy")

    HERE.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".e0-staging-", dir=HERE) as temporary:
        staging = Path(temporary)
        staged_evidence = staging / "evidence"
        staged_evidence.mkdir(mode=0o755)
        staged_snapshot = staged_evidence / SNAPSHOT_ROOT.name
        copy_regular_tree(SOURCE_ROOT, staged_snapshot, source_before)

        snapshot_tree = scan_tree(staged_snapshot)
        snapshot_semantics = audit_v1_semantics(staged_snapshot, snapshot_tree)
        source_after = scan_tree(SOURCE_ROOT)

        gates = {
            "source_semantics_pass_before_copy": source_semantics["gate_pass"],
            "source_bytes_and_modes_unchanged_during_registration": (
                source_before == source_after
            ),
            "snapshot_path_mode_size_and_bytes_match_source": stable_byte_identity(
                source_before, snapshot_tree
            ),
            "snapshot_semantics_independently_pass": snapshot_semantics["gate_pass"],
            "source_and_snapshot_are_disjoint": (
                not SOURCE_ROOT.is_relative_to(staged_snapshot)
                and not staged_snapshot.is_relative_to(SOURCE_ROOT)
            ),
        }
        if not all(gates.values()):
            _fail("E0 registration gates failed before publication")

        manifest = {
            "schema_version": 1,
            "evidence_id": "HF3D_P0P_V1_TERMINAL_FAILURE_SNAPSHOT_E0",
            "status": "HF3D_P0P_V1_FAILURE_EVIDENCE_REGISTERED_IMMUTABLY",
            "source_role": "IGNORED_CONSUMED_V1_RAW_READ_ONLY",
            "source_path": _relative(SOURCE_ROOT),
            "snapshot_role": "TRACKED_BYTE_FOR_BYTE_FAILURE_EVIDENCE_COPY",
            "snapshot_path": _relative(SNAPSHOT_ROOT),
            "source_before": source_before,
            "snapshot_copy": snapshot_tree,
            "source_after": source_after,
            "copy_identity_policy": (
                "path, Git executable mode, byte length, SHA-256, and canonical "
                "tree SHA-256 must match; full source POSIX modes are recorded "
                "while tracked non-executable copies use checkout-stable 0644"
            ),
        }
        audit = {
            "schema_version": 1,
            "status": "HF3D_P0P_V1_E0_REGISTRATION_PASS",
            "gate_pass": True,
            "gates": gates,
            "source_semantics": source_semantics,
            "snapshot_semantics": snapshot_semantics,
            "immutability_statement": (
                "The ignored source was opened read-only, scanned before and "
                "after copying, and was not moved, edited, reformatted, or "
                "augmented. The tracked snapshot is evidence only and is not a "
                "P0-P2 runtime input."
            ),
            "conclusion_ceiling": {
                "v1_claim": "CONSUMED",
                "v1_schedule": "TERMINAL_FAILED_STOPPED_NO_RESUME",
                "v1_measured_performance_result": "NOT_AVAILABLE",
                "p0p2_dynamic_execution": "NOT_AUTHORIZED",
            },
        }
        _write_exclusive(
            staged_evidence / MANIFEST_PATH.name, canonical_json_bytes(manifest)
        )
        _write_exclusive(staged_evidence / AUDIT_PATH.name, canonical_json_bytes(audit))
        os.replace(staged_evidence, EVIDENCE_ROOT)
    return audit


def check_registration() -> dict[str, Any]:
    manifest = strict_json_path(MANIFEST_PATH)
    stored_audit = strict_json_path(AUDIT_PATH)
    source_current = scan_tree(SOURCE_ROOT)
    snapshot_current = scan_tree(SNAPSHOT_ROOT)
    source_semantics = audit_v1_semantics(SOURCE_ROOT, source_current)
    snapshot_semantics = audit_v1_semantics(SNAPSHOT_ROOT, snapshot_current)

    gates = {
        "stored_manifest_status_exact": (
            manifest.get("status")
            == "HF3D_P0P_V1_FAILURE_EVIDENCE_REGISTERED_IMMUTABLY"
        ),
        "stored_audit_pass": (
            stored_audit.get("status") == "HF3D_P0P_V1_E0_REGISTRATION_PASS"
            and stored_audit.get("gate_pass") is True
        ),
        "source_current_matches_registered_source_after": (
            source_current == manifest.get("source_after")
        ),
        "snapshot_current_matches_registered_snapshot": (
            snapshot_current == manifest.get("snapshot_copy")
        ),
        "source_snapshot_byte_identity_still_exact": stable_byte_identity(
            source_current, snapshot_current
        ),
        "source_semantics_still_pass": source_semantics["gate_pass"],
        "snapshot_semantics_still_pass": snapshot_semantics["gate_pass"],
    }
    return {
        "schema_version": 1,
        "status": (
            "HF3D_P0P_V1_E0_REGISTRATION_CHECK_PASS"
            if all(gates.values())
            else "EVIDENCE_REGISTRATION_FAILED_STOPPED"
        ),
        "gate_pass": all(gates.values()),
        "gates": gates,
        "source_canonical_tree_sha256": source_current["canonical_tree_sha256"],
        "snapshot_canonical_tree_sha256": snapshot_current["canonical_tree_sha256"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--register", action="store_true")
    mode.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    try:
        report = register_evidence() if arguments.register else check_registration()
    except EvidenceRegistrationError as exc:
        print(
            canonical_json_bytes(
                {
                    "schema_version": 1,
                    "status": "EVIDENCE_REGISTRATION_FAILED_STOPPED",
                    "gate_pass": False,
                    "message": str(exc),
                }
            ).decode("utf-8"),
            end="",
        )
        return 2
    print(canonical_json_bytes(report).decode("utf-8"), end="")
    return 0 if report.get("gate_pass") is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
