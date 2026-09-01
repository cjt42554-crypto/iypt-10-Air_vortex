"""One-claim, no-resume schedule ledger and worker-launch authentication."""

from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import os
import re
import secrets
import stat
import time
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from p0p2v2_authorization import fixed_schedule_manifest
from p0p2v2_contract import (
    CLAIM_NAMESPACE,
    EXPANDED_EXECUTION_SCHEDULE,
    LEDGER_SCHEMA_ID,
    LOCK_PATH,
    RAW_ROOT,
    P0PerformanceError,
    atomic_json,
    canonical_json_bytes,
    strict_json,
)

CLAIM_PATH = RAW_ROOT / "execution_claim.json"
LEDGER_PATH = RAW_ROOT / "schedule_ledger.json"
FAILURE_PATH = RAW_ROOT / "execution_failure.json"
COMPLETE_PATH = RAW_ROOT / "execution_complete.json"
CAMPAIGN_LOCK_PATH = LOCK_PATH
_ENTRY_STATES = {"NOT_STARTED", "RUNNING", "PASS", "FAILED_STOPPED"}
_LEDGER_STATUSES = {"CLAIMED_NOT_STARTED", "IN_PROGRESS", "COMPLETE", "FAILED_STOPPED"}
_LEDGER_FIELDS = frozenset(
    {
        "schema_version",
        "ledger_schema_id",
        "status",
        "claim_sha256",
        "fixed_schedule_id",
        "fixed_schedule_sha256",
        "entries",
        "failure",
    }
)
_ENTRY_FIELDS = frozenset(
    {
        "sequence_index",
        "sample_id",
        "state",
        "start_count",
        "launch_token_sha256",
        "started_monotonic_ns",
        "completed_monotonic_ns",
        "manifest_sha256",
        "failure",
    }
)
_CLAIM_FIELDS = frozenset(
    {
        "schema_version",
        "claim_namespace",
        "ledger_schema_id",
        "status",
        "authorization_child_commit",
        "review_parent_commit",
        "reviewed_snapshot_commit",
        "ticket_id",
        "ticket_blob",
        "ticket_sha256",
        "authorized_blob",
        "authorized_content_sha256",
        "grant_nonce",
        "fixed_schedule",
        "formal_source_tree",
        "implementation_files",
        "toolchain_binding",
        "gpu_identity",
        "claim_created_at_utc",
        "claim_monotonic_ns",
        "single_writer_lock_identity",
        "maximum_schedule_claims",
        "schedule_resume_after_claim",
        "schedule_retry_or_supplement",
        "sample_replacement",
    }
)
_LOCK_FIELDS = frozenset(
    {
        "pid",
        "process_start_ticks",
        "acquired_monotonic_ns",
        "lock_path",
        "lock_device",
        "lock_inode",
    }
)
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_GIT_OID = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(
        path,
        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _ensure_raw_root() -> None:
    parent = RAW_ROOT.parent
    if not parent.is_dir() or parent.is_symlink():
        raise P0PerformanceError("P0 artifact parent is unavailable or a symlink")
    if RAW_ROOT.is_symlink():
        raise P0PerformanceError("performance raw root must not be a symlink")
    if not RAW_ROOT.exists():
        os.mkdir(RAW_ROOT, 0o700)
        _fsync_directory(parent)
    if not RAW_ROOT.is_dir():
        raise P0PerformanceError("performance raw root is not a real directory")


def create_exclusive_json(path: Path, value: Mapping[str, Any]) -> dict[str, Any]:
    """Create and fsync one final-path JSON object exactly once with O_EXCL."""

    if path.parent != RAW_ROOT:
        raise P0PerformanceError("exclusive runtime record escaped performance raw")
    _ensure_raw_root()
    payload = (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
        0o600,
    )
    try:
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise P0PerformanceError("exclusive runtime record write stopped")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _fsync_directory(RAW_ROOT)
    observed = strict_json(path)
    if observed != dict(value):
        raise P0PerformanceError("exclusive runtime record strict reload changed")
    return {
        "path": str(path),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size_bytes": len(payload),
    }


def _claim_hash() -> str:
    if not CLAIM_PATH.is_file() or CLAIM_PATH.is_symlink():
        raise P0PerformanceError("execution claim is unavailable")
    return hashlib.sha256(CLAIM_PATH.read_bytes()).hexdigest()


def _exact_fields(
    value: Mapping[str, Any], expected: frozenset[str], label: str
) -> None:
    if set(value) != set(expected):
        raise P0PerformanceError(
            f"{label} schema differs; missing={sorted(set(expected) - set(value))!r}, "
            f"unknown={sorted(set(value) - set(expected))!r}"
        )


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and _HEX_64.fullmatch(value) is not None


def _is_git_oid(value: object) -> bool:
    return isinstance(value, str) and _GIT_OID.fullmatch(value) is not None


def validate_claim(claim: Mapping[str, Any]) -> None:
    _exact_fields(claim, _CLAIM_FIELDS, "execution claim")
    schedule = fixed_schedule_manifest()
    lock = claim.get("single_writer_lock_identity")
    gates = {
        "schema_version": claim.get("schema_version") == 2,
        "claim_namespace": claim.get("claim_namespace") == CLAIM_NAMESPACE,
        "ledger_schema_id": claim.get("ledger_schema_id") == LEDGER_SCHEMA_ID,
        "status": claim.get("status")
        == "HF3D_P0P2_V2_SINGLE_SCHEDULE_CLAIMED",
        "commit_ids": all(
            _is_git_oid(claim.get(field))
            for field in (
                "authorization_child_commit",
                "review_parent_commit",
                "reviewed_snapshot_commit",
                "ticket_blob",
                "authorized_blob",
            )
        ),
        "sha256_fields": all(
            _is_sha256(claim.get(field))
            for field in ("ticket_sha256", "authorized_content_sha256")
        ),
        "nonempty_ticket_and_nonce": all(
            isinstance(claim.get(field), str) and bool(str(claim[field]))
            for field in ("ticket_id", "grant_nonce", "claim_created_at_utc")
        ),
        "fixed_schedule_exact": claim.get("fixed_schedule") == schedule,
        "bound_objects_present": all(
            isinstance(claim.get(field), Mapping) and bool(claim[field])
            for field in (
                "formal_source_tree",
                "implementation_files",
                "toolchain_binding",
                "gpu_identity",
            )
        ),
        "claim_monotonic_ns": type(claim.get("claim_monotonic_ns")) is int
        and int(claim["claim_monotonic_ns"]) > 0,
        "lock_identity_schema": isinstance(lock, Mapping)
        and set(lock) == set(_LOCK_FIELDS),
        "one_claim": claim.get("maximum_schedule_claims") == 1,
        "no_resume": claim.get("schedule_resume_after_claim") == "PROHIBITED",
        "no_replacement": claim.get("sample_replacement") == "PROHIBITED",
        "no_retry_or_supplement": claim.get("schedule_retry_or_supplement")
        == "PROHIBITED",
    }
    failed = [name for name, passed in gates.items() if not passed]
    if failed:
        raise P0PerformanceError(
            "execution claim validation failed: " + ", ".join(failed)
        )


def build_claim(
    *,
    authorization: Mapping[str, Any],
    execution_binding: Mapping[str, Any],
    environment: Mapping[str, Any],
    lock_identity: Mapping[str, Any],
) -> dict[str, Any]:
    parent_binding = authorization.get("observed_parent_ticket_binding")
    if (
        not isinstance(parent_binding, Mapping)
        or parent_binding.get("gate_pass") is not True
    ):
        raise P0PerformanceError("authorized parent-ticket binding is unavailable")
    schedule = fixed_schedule_manifest()
    ticket = parent_binding.get("ticket")
    if not isinstance(ticket, Mapping):
        raise P0PerformanceError("parent ticket is missing from authorization binding")
    claim = {
        "schema_version": 2,
        "claim_namespace": CLAIM_NAMESPACE,
        "ledger_schema_id": LEDGER_SCHEMA_ID,
        "status": "HF3D_P0P2_V2_SINGLE_SCHEDULE_CLAIMED",
        "authorization_child_commit": parent_binding["authorization_child_commit"],
        "review_parent_commit": parent_binding["review_parent_commit"],
        "reviewed_snapshot_commit": parent_binding["reviewed_snapshot_commit"],
        "ticket_id": ticket["ticket_id"],
        "ticket_blob": parent_binding["ticket_blob"],
        "ticket_sha256": parent_binding["ticket_sha256"],
        "authorized_blob": parent_binding["authorized_child_blob"],
        "authorized_content_sha256": ticket["authorized_child_blob"]["content_sha256"],
        "grant_nonce": authorization["grant_nonce"],
        "fixed_schedule": schedule,
        "formal_source_tree": execution_binding["formal_source_tree"],
        "implementation_files": execution_binding["implementation_files"],
        "toolchain_binding": ticket["toolchain_binding"],
        "gpu_identity": environment["instantaneous_start"]["snapshot"]["gpu"],
        "claim_created_at_utc": datetime.now(timezone.utc).isoformat(),
        "claim_monotonic_ns": time.monotonic_ns(),
        "single_writer_lock_identity": dict(lock_identity),
        "maximum_schedule_claims": 1,
        "schedule_resume_after_claim": "PROHIBITED",
        "schedule_retry_or_supplement": "PROHIBITED",
        "sample_replacement": "PROHIBITED",
    }
    validate_claim(claim)
    return claim


def create_claim_and_ledger(
    *,
    authorization: Mapping[str, Any],
    execution_binding: Mapping[str, Any],
    environment: Mapping[str, Any],
    lock_identity: Mapping[str, Any],
) -> dict[str, Any]:
    """Consume the authorization once, then initialize the immutable-order ledger."""

    if RAW_ROOT.is_symlink():
        raise P0PerformanceError("performance raw root must not be a symlink")
    if RAW_ROOT.exists() and any(RAW_ROOT.iterdir()):
        raise P0PerformanceError(
            "performance raw already contains a consumed claim/evidence"
        )
    claim = build_claim(
        authorization=authorization,
        execution_binding=execution_binding,
        environment=environment,
        lock_identity=lock_identity,
    )
    claim_file = create_exclusive_json(CLAIM_PATH, claim)
    schedule = fixed_schedule_manifest()
    ledger = {
        "schema_version": 2,
        "ledger_schema_id": LEDGER_SCHEMA_ID,
        "status": "CLAIMED_NOT_STARTED",
        "claim_sha256": claim_file["sha256"],
        "fixed_schedule_id": schedule["schedule_id"],
        "fixed_schedule_sha256": schedule["canonical_schedule_sha256"],
        "entries": [
            {
                "sequence_index": index,
                "sample_id": sample_id,
                "state": "NOT_STARTED",
                "start_count": 0,
                "launch_token_sha256": None,
                "started_monotonic_ns": None,
                "completed_monotonic_ns": None,
                "manifest_sha256": None,
                "failure": None,
            }
            for index, sample_id in enumerate(EXPANDED_EXECUTION_SCHEDULE)
        ],
        "failure": None,
    }
    try:
        ledger_file = create_exclusive_json(LEDGER_PATH, ledger)
        validate_ledger(ledger)
    except BaseException as exc:
        failure = {
            "schema_version": 2,
            "status": "HF3D_P0P2_V2_CLAIM_CONSUMED_LEDGER_INITIALIZATION_FAILED",
            "gate_pass": False,
            "claim_sha256": claim_file["sha256"],
            "exception_type": type(exc).__name__,
            "message": str(exc),
            "failed_monotonic_ns": time.monotonic_ns(),
            "schedule_resume_after_claim": "PROHIBITED",
        }
        if not FAILURE_PATH.exists() and not FAILURE_PATH.is_symlink():
            try:
                create_exclusive_json(FAILURE_PATH, failure)
            except (
                OSError,
                P0PerformanceError,
                TypeError,
                ValueError,
            ) as publication_error:
                # The immutable claim (or its partial O_EXCL inode) remains the
                # primary consumption evidence if the storage failure also
                # prevents publication of the secondary failure record.
                exc.add_note(
                    "secondary failure-record publication also failed: "
                    f"{type(publication_error).__name__}: {publication_error}"
                )
        raise
    return {
        "claim": claim,
        "claim_file": claim_file,
        "ledger": ledger,
        "ledger_file": ledger_file,
    }


def validate_ledger(ledger: Mapping[str, Any]) -> None:
    _exact_fields(ledger, _LEDGER_FIELDS, "schedule ledger")
    entries = ledger.get("entries")
    if not isinstance(entries, list) or len(entries) != len(
        EXPANDED_EXECUTION_SCHEDULE
    ):
        raise P0PerformanceError("schedule ledger entry count differs from frozen 18")
    if not (
        ledger.get("schema_version") == 2
        and ledger.get("ledger_schema_id") == LEDGER_SCHEMA_ID
        and ledger.get("status") in _LEDGER_STATUSES
        and _is_sha256(ledger.get("claim_sha256"))
    ):
        raise P0PerformanceError("schedule ledger top-level identity is invalid")
    states: list[str] = []
    for index, (entry, sample_id) in enumerate(
        zip(entries, EXPANDED_EXECUTION_SCHEDULE, strict=True)
    ):
        if not isinstance(entry, Mapping):
            raise P0PerformanceError("schedule ledger entry is not an object")
        _exact_fields(entry, _ENTRY_FIELDS, f"schedule ledger entry {index}")
        if not (
            entry.get("sequence_index") == index
            and entry.get("sample_id") == sample_id
            and entry.get("state") in _ENTRY_STATES
            and type(entry.get("start_count")) is int
            and 0 <= int(entry["start_count"]) <= 1
        ):
            raise P0PerformanceError("schedule ledger identity/start count is invalid")
        state = str(entry["state"])
        states.append(state)
        token = entry.get("launch_token_sha256")
        started = entry.get("started_monotonic_ns")
        completed = entry.get("completed_monotonic_ns")
        manifest = entry.get("manifest_sha256")
        failure = entry.get("failure")
        if state == "NOT_STARTED":
            valid_state = (
                entry["start_count"] == 0
                and token is None
                and started is None
                and completed is None
                and manifest is None
                and failure is None
            )
        elif state == "RUNNING":
            valid_state = (
                entry["start_count"] == 1
                and _is_sha256(token)
                and type(started) is int
                and int(started) > 0
                and completed is None
                and manifest is None
                and failure is None
            )
        elif state == "PASS":
            valid_state = (
                entry["start_count"] == 1
                and _is_sha256(token)
                and type(started) is int
                and type(completed) is int
                and 0 < int(started) <= int(completed)
                and _is_sha256(manifest)
                and failure is None
            )
        else:
            valid_state = (
                entry["start_count"] == 1
                and _is_sha256(token)
                and type(started) is int
                and type(completed) is int
                and 0 < int(started) <= int(completed)
                and manifest is None
                and isinstance(failure, Mapping)
                and bool(failure)
            )
        if not valid_state:
            raise P0PerformanceError(
                f"schedule ledger state payload is invalid at index {index}"
            )
    pass_count = 0
    while pass_count < len(states) and states[pass_count] == "PASS":
        pass_count += 1
    suffix = states[pass_count:]
    status = ledger.get("status")
    if status == "CLAIMED_NOT_STARTED":
        shape_pass = pass_count == 0 and all(state == "NOT_STARTED" for state in suffix)
    elif status == "IN_PROGRESS":
        shape_pass = bool(suffix) and (
            (pass_count > 0 and all(state == "NOT_STARTED" for state in suffix))
            or (
                suffix[0] == "RUNNING"
                and all(state == "NOT_STARTED" for state in suffix[1:])
            )
        )
    elif status == "COMPLETE":
        shape_pass = pass_count == len(states) and not suffix
    else:
        shape_pass = pass_count < len(states) and (
            all(state == "NOT_STARTED" for state in suffix)
            or (
                bool(suffix)
                and suffix[0] == "FAILED_STOPPED"
                and all(state == "NOT_STARTED" for state in suffix[1:])
            )
        )
    if not shape_pass:
        raise P0PerformanceError(
            "schedule ledger states are not one legal no-resume prefix"
        )
    global_failure = ledger.get("failure")
    if (status == "FAILED_STOPPED") != (
        isinstance(global_failure, Mapping) and bool(global_failure)
    ):
        raise P0PerformanceError("schedule ledger global failure state is invalid")
    if ledger.get("fixed_schedule_id") != fixed_schedule_manifest()["schedule_id"]:
        raise P0PerformanceError("schedule ledger ID drifted")
    if (
        ledger.get("fixed_schedule_sha256")
        != fixed_schedule_manifest()["canonical_schedule_sha256"]
    ):
        raise P0PerformanceError("schedule ledger hash drifted")


def load_ledger() -> dict[str, Any]:
    claim = strict_json(CLAIM_PATH)
    validate_claim(claim)
    ledger = strict_json(LEDGER_PATH)
    validate_ledger(ledger)
    if ledger.get("claim_sha256") != _claim_hash():
        raise P0PerformanceError("schedule ledger is bound to another claim")
    return ledger


def _publish_ledger(ledger: Mapping[str, Any]) -> None:
    validate_ledger(ledger)
    if LEDGER_PATH.is_symlink() or not LEDGER_PATH.is_file():
        raise P0PerformanceError("schedule ledger path is unavailable")
    atomic_json(LEDGER_PATH, ledger)
    _fsync_directory(RAW_ROOT)
    if strict_json(LEDGER_PATH) != dict(ledger):
        raise P0PerformanceError("schedule ledger strict reload changed")


def mark_sample_running(sequence_index: int, sample_id: str) -> str:
    ledger = load_ledger()
    if ledger.get("status") not in {"CLAIMED_NOT_STARTED", "IN_PROGRESS"}:
        raise P0PerformanceError("consumed/terminal schedule cannot start a sample")
    entries = ledger["entries"]
    if not (0 <= sequence_index < len(entries)):
        raise P0PerformanceError("sample sequence index is outside frozen schedule")
    expected = EXPANDED_EXECUTION_SCHEDULE[sequence_index]
    if sample_id != expected:
        raise P0PerformanceError("sample ID differs from frozen sequence index")
    if any(entry["state"] != "PASS" for entry in entries[:sequence_index]):
        raise P0PerformanceError("sample launch would skip a non-PASS predecessor")
    if entries[sequence_index]["state"] != "NOT_STARTED":
        raise P0PerformanceError("sample was already started or is not startable")
    if any(entry["state"] != "NOT_STARTED" for entry in entries[sequence_index + 1 :]):
        raise P0PerformanceError("future ledger suffix was already modified")
    token = secrets.token_urlsafe(48)
    entry = entries[sequence_index]
    entry["state"] = "RUNNING"
    entry["start_count"] = 1
    entry["launch_token_sha256"] = hashlib.sha256(token.encode("utf-8")).hexdigest()
    entry["started_monotonic_ns"] = time.monotonic_ns()
    ledger["status"] = "IN_PROGRESS"
    _publish_ledger(ledger)
    return token


def mark_sample_pass(
    sequence_index: int,
    sample_id: str,
    *,
    launch_token: str,
    manifest_sha256: str,
) -> dict[str, Any]:
    ledger = load_ledger()
    if not (0 <= sequence_index < len(EXPANDED_EXECUTION_SCHEDULE)):
        raise P0PerformanceError("sample PASS sequence index is invalid")
    if not _is_sha256(manifest_sha256):
        raise P0PerformanceError("sample manifest SHA-256 is invalid")
    entry = ledger["entries"][sequence_index]
    token_sha = hashlib.sha256(launch_token.encode("utf-8")).hexdigest()
    if not (
        entry["sample_id"] == sample_id
        and entry["state"] == "RUNNING"
        and entry["launch_token_sha256"] == token_sha
    ):
        raise P0PerformanceError(
            "sample PASS transition lacks its one-time launch token"
        )
    entry["state"] = "PASS"
    entry["completed_monotonic_ns"] = time.monotonic_ns()
    entry["manifest_sha256"] = manifest_sha256
    ledger["status"] = (
        "COMPLETE"
        if sequence_index + 1 == len(EXPANDED_EXECUTION_SCHEDULE)
        else "IN_PROGRESS"
    )
    _publish_ledger(ledger)
    return ledger


def mark_schedule_failed(
    *,
    sequence_index: int | None,
    sample_id: str | None,
    failure: Mapping[str, Any],
) -> dict[str, Any]:
    if not failure:
        raise P0PerformanceError("schedule failure record must be non-empty")
    ledger = load_ledger()
    if ledger.get("status") in {"COMPLETE", "FAILED_STOPPED"}:
        raise P0PerformanceError("terminal schedule state cannot be replaced")
    if sequence_index is not None:
        if not (0 <= sequence_index < len(EXPANDED_EXECUTION_SCHEDULE)):
            raise P0PerformanceError("failed sample sequence index is invalid")
        entry = ledger["entries"][sequence_index]
        if entry["sample_id"] != sample_id or entry["state"] != "RUNNING":
            raise P0PerformanceError(
                "failed sample does not match RUNNING ledger entry"
            )
        entry["state"] = "FAILED_STOPPED"
        entry["completed_monotonic_ns"] = time.monotonic_ns()
        entry["failure"] = dict(failure)
    elif any(entry["state"] == "RUNNING" for entry in ledger["entries"]):
        raise P0PerformanceError("global failure omitted the RUNNING sample identity")
    ledger["status"] = "FAILED_STOPPED"
    ledger["failure"] = dict(failure)
    _publish_ledger(ledger)
    return ledger


def _process_start_ticks(pid: int) -> int:
    try:
        raw = (Path("/proc") / str(pid) / "stat").read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise P0PerformanceError("orchestrator PID stat is unavailable") from exc
    close = raw.rfind(")")
    if close < 0:
        raise P0PerformanceError("orchestrator PID stat is invalid")
    fields = raw[close + 2 :].split()
    if len(fields) < 20:
        raise P0PerformanceError("orchestrator PID stat is too short")
    try:
        return int(fields[19])
    except ValueError as exc:
        raise P0PerformanceError("orchestrator PID start ticks are invalid") from exc


def _proc_lock_owned_by(lock_stat: os.stat_result, pid: int) -> bool:
    try:
        lines = Path("/proc/locks").read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise P0PerformanceError("/proc/locks is unavailable") from exc
    expected_major = os.major(lock_stat.st_dev)
    expected_minor = os.minor(lock_stat.st_dev)
    for line in lines:
        fields = line.split()
        if len(fields) < 8 or fields[1] == "->":
            continue
        if fields[1:4] != ["FLOCK", "ADVISORY", "WRITE"]:
            continue
        try:
            owner = int(fields[4])
            device, inode_text = fields[5].rsplit(":", 1)
            major_text, minor_text = device.split(":", 1)
            major = int(major_text, 16)
            minor = int(minor_text, 16)
            inode = int(inode_text)
        except (ValueError, IndexError):
            continue
        if (
            owner == pid
            and major == expected_major
            and minor == expected_minor
            and inode == lock_stat.st_ino
        ):
            return True
    return False


def _validate_live_lock(
    lock_path: Path,
    *,
    orchestrator_pid: int,
    expected_lock: Mapping[str, Any],
) -> dict[str, Any]:
    lock = strict_json(lock_path)
    _exact_fields(lock, _LOCK_FIELDS, "campaign lock identity")
    descriptor = os.open(
        lock_path,
        os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
    )
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
            raise P0PerformanceError("campaign lock is not one regular file")
        blocked = False
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno not in (errno.EACCES, errno.EAGAIN):
                raise
            blocked = True
        else:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        if not blocked:
            raise P0PerformanceError("campaign lock file is not actively locked")
    finally:
        os.close(descriptor)
    if not (
        lock == dict(expected_lock)
        and lock.get("pid") == orchestrator_pid
        and lock.get("lock_path") == str(lock_path.absolute())
        and lock.get("lock_device") == opened.st_dev
        and lock.get("lock_inode") == opened.st_ino
        and _process_start_ticks(orchestrator_pid) == lock.get("process_start_ticks")
        and _proc_lock_owned_by(opened, orchestrator_pid)
    ):
        raise P0PerformanceError("worker live-lock owner/PID-reuse gate failed")
    return dict(lock)


def _consume_worker_launch(
    staging_path: Path,
    *,
    sample_id: str,
    sequence_index: int,
    token_sha256: str,
    claim_sha256: str,
    orchestrator_pid: int,
) -> dict[str, Any]:
    if staging_path.is_symlink():
        raise P0PerformanceError("worker launch staging must not be a symlink")
    staging = staging_path.absolute()
    if not (
        staging.parent.resolve() == RAW_ROOT.resolve()
        and staging.name.startswith(f".{sample_id}.staging.")
        and staging.is_dir()
        and not staging.is_symlink()
    ):
        raise P0PerformanceError("worker launch staging identity is invalid")
    worker_pid = os.getpid()
    receipt = {
        "schema_version": 2,
        "status": "HF3D_P0P2_V2_WORKER_LAUNCH_TOKEN_CONSUMED",
        "sample_id": sample_id,
        "sequence_index": sequence_index,
        "launch_token_sha256": token_sha256,
        "claim_sha256": claim_sha256,
        "orchestrator_pid": orchestrator_pid,
        "worker_pid": worker_pid,
        "worker_process_start_ticks": _process_start_ticks(worker_pid),
        "consumed_monotonic_ns": time.monotonic_ns(),
    }
    path = staging / "worker_launch_claim.json"
    payload = (
        json.dumps(
            receipt,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
        0o600,
    )
    try:
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise P0PerformanceError("worker launch receipt write stopped")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _fsync_directory(staging)
    if strict_json(path) != receipt:
        raise P0PerformanceError("worker launch receipt strict reload changed")
    return {
        **receipt,
        "receipt_path": str(RAW_ROOT / sample_id / path.name),
    }


def validate_worker_launch(
    *,
    sample_id: str,
    sequence_index: int,
    launch_token: str,
    orchestrator_pid: int,
    lock_path: Path,
    staging_path: Path,
) -> dict[str, Any]:
    """Reject direct workers unless the current claim/ledger/lock authorize one start."""

    claim = strict_json(CLAIM_PATH)
    validate_claim(claim)
    ledger = load_ledger()
    if not (0 <= sequence_index < len(EXPANDED_EXECUTION_SCHEDULE)):
        raise P0PerformanceError("worker sequence index is invalid")
    entry = ledger["entries"][sequence_index]
    token_sha = hashlib.sha256(launch_token.encode("utf-8")).hexdigest()
    if not (
        sample_id == EXPANDED_EXECUTION_SCHEDULE[sequence_index]
        and entry["sample_id"] == sample_id
        and entry["state"] == "RUNNING"
        and entry["start_count"] == 1
        and entry["launch_token_sha256"] == token_sha
    ):
        raise P0PerformanceError("worker launch token/ledger identity failed")
    expected_lock = claim.get("single_writer_lock_identity")
    if not isinstance(expected_lock, Mapping):
        raise P0PerformanceError("execution claim lock identity is missing")
    lock = _validate_live_lock(
        lock_path,
        orchestrator_pid=orchestrator_pid,
        expected_lock=expected_lock,
    )
    claim_sha = _claim_hash()
    receipt = _consume_worker_launch(
        staging_path,
        sample_id=sample_id,
        sequence_index=sequence_index,
        token_sha256=token_sha,
        claim_sha256=claim_sha,
        orchestrator_pid=orchestrator_pid,
    )
    return {
        "gate_pass": True,
        "sample_id": sample_id,
        "sequence_index": sequence_index,
        "claim_sha256": claim_sha,
        "launch_token_sha256": token_sha,
        "orchestrator_pid": orchestrator_pid,
        "orchestrator_start_ticks": lock["process_start_ticks"],
        "claim_gpu_identity": claim["gpu_identity"],
        "claim_toolchain_binding": claim["toolchain_binding"],
        "worker_launch_receipt": receipt,
    }


def publish_execution_failure(record: Mapping[str, Any]) -> dict[str, Any]:
    ledger = load_ledger()
    if ledger.get("status") != "FAILED_STOPPED":
        raise P0PerformanceError(
            "execution failure record requires a terminal failed ledger"
        )
    if COMPLETE_PATH.exists() or COMPLETE_PATH.is_symlink():
        raise P0PerformanceError("completion evidence already exists")
    if not (
        record.get("schema_version") == 2
        and record.get("gate_pass") is False
        and isinstance(record.get("status"), str)
        and bool(record["status"])
    ):
        raise P0PerformanceError("execution failure record identity is invalid")
    return create_exclusive_json(FAILURE_PATH, record)


def publish_execution_complete(record: Mapping[str, Any]) -> dict[str, Any]:
    ledger = load_ledger()
    if ledger.get("status") != "COMPLETE":
        raise P0PerformanceError("execution cannot complete before all 18 PASS")
    if FAILURE_PATH.exists() or FAILURE_PATH.is_symlink():
        raise P0PerformanceError("failure evidence already exists")
    claim = strict_json(CLAIM_PATH)
    if not (
        set(record)
        == {
            "schema_version",
            "status",
            "gate_pass",
            "authorization_child_commit",
            "completed_samples",
            "completed_monotonic_ns",
        }
        and record.get("schema_version") == 2
        and record.get("status")
        == "HF3D_P0P2_V2_FROZEN_SCHEDULE_EXECUTED_PENDING_ANALYSIS"
        and record.get("gate_pass") is True
        and record.get("authorization_child_commit")
        == claim.get("authorization_child_commit")
        and record.get("completed_samples") == list(EXPANDED_EXECUTION_SCHEDULE)
        and type(record.get("completed_monotonic_ns")) is int
        and int(record["completed_monotonic_ns"]) > 0
    ):
        raise P0PerformanceError("execution completion record identity is invalid")
    return create_exclusive_json(COMPLETE_PATH, record)


def claim_payload_sha256(value: Mapping[str, Any]) -> str:
    """Stable helper for synthetic tests; file hashes include pretty-print whitespace."""

    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


__all__ = [
    "CAMPAIGN_LOCK_PATH",
    "CLAIM_PATH",
    "COMPLETE_PATH",
    "FAILURE_PATH",
    "LEDGER_PATH",
    "build_claim",
    "claim_payload_sha256",
    "create_claim_and_ledger",
    "create_exclusive_json",
    "load_ledger",
    "mark_sample_pass",
    "mark_sample_running",
    "mark_schedule_failed",
    "publish_execution_complete",
    "publish_execution_failure",
    "validate_claim",
    "validate_ledger",
    "validate_worker_launch",
]
