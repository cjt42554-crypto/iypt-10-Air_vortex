"""Parent-anchored exact-blob authorization for the HF3D P0-P2 V2 run."""

from __future__ import annotations

import hashlib
import re
import subprocess
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from p0p2v2_contract import (
    AUTHORIZATION_ID,
    AUTHORIZATION_PATH,
    AUTHORIZED_STATUS,
    CONTRACT_ID,
    FORMAL_SOURCE_SHA256,
    GRANT_NONCE_PREFIX,
    HERE,
    NOT_AUTHORIZED_STATUS,
    REPOSITORY_ROOT,
    SCHEDULE_ID,
    TICKET_PATH,
    P0PerformanceError,
    relative,
    strict_json,
    strict_json_bytes,
)
from p0p2v2_contract import (
    fixed_schedule_manifest as contract_fixed_schedule_manifest,
)

TICKET_RELATIVE_PATH = relative(TICKET_PATH)
AUTHORIZATION_RELATIVE_PATH = relative(AUTHORIZATION_PATH)
CURRENT_RELATIVE_PATH = "90_候选工作区/交接说明_current.md"
HISTORY_RELATIVE_PATH = "90_候选工作区/交接说明_history.md"
REQUIRED_IMPLEMENTATION_RELATIVE_PATHS = frozenset(
    relative(HERE / name)
    for name in (
        ".gitignore",
        "analyse_p0p2v2_campaign.py",
        "audit_p0p2v2_harness.py",
        "p0p2v2_contract.py",
        "p0p2v2_allocation.py",
        "p0p2v2_allocator_observer.c",
        "p0p2v2_allocator_probe.c",
        "p0p2v2_allocator_probe",
        "libp0p2v2_allocator_observer.so",
        "p0p2v2_authorization.py",
        "p0p2v2_capability_probe.py",
        "p0p2v2_consumption.py",
        "p0p2v2_cuda_mem_exact.py",
        "p0p2v2_execution_authorization.json",
        "p0p2v2_instrumentation.py",
        "p0p2v2_monitor.py",
        "p0p2v2_nsys.py",
        "p0p2v2_numerical.py",
        "p0p2v2_telemetry.py",
        "p0p2v2_transaction.py",
        "p0p2v2_zero_step_probe.py",
        "run_p0p2v2_campaign.py",
        "run_p0p2v2_sample.py",
        "p0p2v2_contract.json",
        "build_p0p2v2_contract.py",
        "tests/test_p0p2v2_authorization_and_consumption.py",
        "tests/test_p0p2v2_harness.py",
        "tests/test_p0p2v2_git_topology.py",
        "generated_effective_configs/P0P2V2_B1.json",
        "generated_effective_configs/P0P2V2_B2.json",
        "generated_effective_configs/P0P2V2_B3.json",
    )
)
FIXED_SCHEDULE_ID = SCHEDULE_ID
TICKET_STATUS = "READY_FOR_SINGLE_AUTHORIZATION_CHILD"
FORBIDDEN_SELF_FIELDS = frozenset(
    {
        "authorization_child_commit",
        "current_head_commit",
        "self_commit_hash",
        "self_blob_hash",
        "review_parent_commit",
        "authorization_commit",
        "reviewed_parent",
    }
)
DENIED_FIELDS = frozenset(
    {
        "schema_version",
        "authorization_id",
        "contract_id",
        "status",
        "dynamic_samples_authorized",
        "grant_nonce",
        "reviewed_snapshot_commit",
        "ticket_id",
        "ticket_path",
        "fixed_schedule_id",
        "fixed_schedule_sha256",
        "maximum_schedule_claims",
        "preflight_consumes_claim",
        "claim_before_first_sample",
        "sample_replacement",
        "schedule_resume_after_claim",
        "schedule_retry_or_supplement",
        "P1",
        "reason",
    }
)
AUTHORIZED_FIELDS = frozenset(
    {
        "schema_version",
        "authorization_id",
        "contract_id",
        "ticket_id",
        "ticket_path",
        "status",
        "dynamic_samples_authorized",
        "grant_nonce",
        "reviewed_snapshot_commit",
        "fixed_schedule_id",
        "fixed_schedule_sha256",
        "maximum_schedule_claims",
        "preflight_consumes_claim",
        "claim_before_first_sample",
        "sample_replacement",
        "schedule_resume_after_claim",
        "schedule_retry_or_supplement",
        "P1",
    }
)
TICKET_FIELDS = frozenset(
    {
        "schema_version",
        "ticket_id",
        "contract_id",
        "status",
        "authorization_path",
        "ruling_path",
        "reviewed_snapshot",
        "review_parent_policy",
        "denied_parent_blob",
        "authorized_child_blob",
        "fixed_schedule",
        "child_commit_policy",
        "claim_policy",
        "toolchain_binding",
        "formal_source_tree_sha256",
    }
)
_BLOB_RECORD_FIELDS = frozenset(
    {
        "git_oid",
        "git_object_algorithm",
        "content_sha256",
        "byte_length",
        "encoding",
        "line_endings",
        "mode",
    }
)
_BOUND_FILE_FIELDS = frozenset({"path", "git_blob", "sha256", "byte_length", "mode"})
_GIT_OID = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ALWAYS_PROTECTED_REVIEW_PREFIXES = (
    "01_最终理论/",
    "02_模拟程序/",
    "90_候选工作区/current/07_D3跨网格时间步响应诊断/",
)


def _validate_repository_path(path: object, label: str) -> str:
    if not isinstance(path, str) or not path or "\0" in path:
        raise P0PerformanceError(f"{label} path is invalid")
    parsed = PurePosixPath(path)
    if parsed.is_absolute() or parsed.as_posix() != path or ".." in parsed.parts:
        raise P0PerformanceError(f"{label} path is not normalized repository-relative")
    return path


def _exact_keys(value: Mapping[str, Any], expected: frozenset[str], label: str) -> None:
    actual = set(value)
    if actual != set(expected):
        missing = sorted(set(expected) - actual)
        unknown = sorted(actual - set(expected))
        raise P0PerformanceError(
            f"{label} fields differ; missing={missing!r}, unknown={unknown!r}"
        )


def fixed_schedule_manifest() -> dict[str, Any]:
    """Return the complete canonical 18-item schedule bound by a future ticket."""
    return contract_fixed_schedule_manifest()


def default_denied_authorization() -> dict[str, Any]:
    schedule = fixed_schedule_manifest()
    return {
        "schema_version": 3,
        "authorization_id": AUTHORIZATION_ID,
        "contract_id": CONTRACT_ID,
        "status": NOT_AUTHORIZED_STATUS,
        "dynamic_samples_authorized": False,
        "grant_nonce": None,
        "reviewed_snapshot_commit": None,
        "ticket_id": None,
        "ticket_path": TICKET_RELATIVE_PATH,
        "fixed_schedule_id": FIXED_SCHEDULE_ID,
        "fixed_schedule_sha256": schedule["canonical_schedule_sha256"],
        "maximum_schedule_claims": 0,
        "preflight_consumes_claim": False,
        "claim_before_first_sample": True,
        "sample_replacement": "PROHIBITED",
        "schedule_resume_after_claim": "PROHIBITED",
        "schedule_retry_or_supplement": "PROHIBITED",
        "P1": "PROHIBITED",
        "reason": "P0-P2 V2 harness awaits independent Pro review and exact P2/C2 authorization",
    }


def validate_denied_authorization(value: Mapping[str, Any]) -> dict[str, Any]:
    _exact_keys(value, DENIED_FIELDS, "denied authorization")
    if FORBIDDEN_SELF_FIELDS & set(value):
        raise P0PerformanceError("denied authorization contains a self reference")
    schedule = fixed_schedule_manifest()
    gates = {
        "schema_version": value.get("schema_version") == 3,
        "authorization_id": value.get("authorization_id") == AUTHORIZATION_ID,
        "contract_id": value.get("contract_id") == CONTRACT_ID,
        "status": value.get("status") == NOT_AUTHORIZED_STATUS,
        "dynamic_samples_denied": value.get("dynamic_samples_authorized") is False,
        "grant_nonce_absent": value.get("grant_nonce") is None,
        "reviewed_snapshot_absent": value.get("reviewed_snapshot_commit") is None,
        "ticket_not_issued": value.get("ticket_id") is None,
        "ticket_path": value.get("ticket_path") == TICKET_RELATIVE_PATH,
        "fixed_schedule_id": value.get("fixed_schedule_id") == FIXED_SCHEDULE_ID,
        "fixed_schedule_sha256": value.get("fixed_schedule_sha256")
        == schedule["canonical_schedule_sha256"],
        "maximum_claims_zero": value.get("maximum_schedule_claims") == 0,
        "preflight_nonconsuming": value.get("preflight_consumes_claim") is False,
        "claim_before_first_sample": value.get("claim_before_first_sample") is True,
        "replacement_prohibited": value.get("sample_replacement") == "PROHIBITED",
        "resume_prohibited": value.get("schedule_resume_after_claim") == "PROHIBITED",
        "retry_prohibited": value.get("schedule_retry_or_supplement")
        == "PROHIBITED",
        "p1_prohibited": value.get("P1") == "PROHIBITED",
        "reason_nonempty": isinstance(value.get("reason"), str)
        and bool(str(value["reason"]).strip()),
    }
    failed = [name for name, passed in gates.items() if not passed]
    if failed:
        raise P0PerformanceError(
            "default-denial authorization failed: " + ", ".join(failed)
        )
    return dict(value)


def validate_authorized_grant(value: Mapping[str, Any]) -> dict[str, Any]:
    _exact_keys(value, AUTHORIZED_FIELDS, "authorized grant")
    if FORBIDDEN_SELF_FIELDS & set(value):
        raise P0PerformanceError("authorized grant contains a self reference")
    schedule = fixed_schedule_manifest()
    gates = {
        "schema_version": value.get("schema_version") == 3,
        "authorization_id": value.get("authorization_id") == AUTHORIZATION_ID,
        "contract_id": value.get("contract_id") == CONTRACT_ID,
        "ticket_id": isinstance(value.get("ticket_id"), str)
        and bool(str(value["ticket_id"]).strip()),
        "ticket_path": value.get("ticket_path") == TICKET_RELATIVE_PATH,
        "status": value.get("status") == AUTHORIZED_STATUS,
        "dynamic_samples_authorized": value.get("dynamic_samples_authorized") is True,
        "grant_nonce": isinstance(value.get("grant_nonce"), str)
        and str(value["grant_nonce"]).startswith(GRANT_NONCE_PREFIX)
        and len(str(value["grant_nonce"])) >= len(GRANT_NONCE_PREFIX) + 32
        and "\n" not in str(value["grant_nonce"]),
        "reviewed_snapshot_commit": isinstance(
            value.get("reviewed_snapshot_commit"), str
        )
        and _GIT_OID.fullmatch(str(value["reviewed_snapshot_commit"])) is not None,
        "fixed_schedule_id": value.get("fixed_schedule_id") == FIXED_SCHEDULE_ID,
        "fixed_schedule_sha256": value.get("fixed_schedule_sha256")
        == schedule["canonical_schedule_sha256"],
        "maximum_claims_one": value.get("maximum_schedule_claims") == 1,
        "preflight_nonconsuming": value.get("preflight_consumes_claim") is False,
        "claim_before_first_sample": value.get("claim_before_first_sample") is True,
        "replacement_prohibited": value.get("sample_replacement") == "PROHIBITED",
        "resume_prohibited": value.get("schedule_resume_after_claim") == "PROHIBITED",
        "retry_prohibited": value.get("schedule_retry_or_supplement")
        == "PROHIBITED",
        "p1_prohibited": value.get("P1") == "PROHIBITED",
    }
    failed = [name for name, passed in gates.items() if not passed]
    if failed:
        raise P0PerformanceError("authorized grant failed: " + ", ".join(failed))
    return dict(value)


def _git(
    repository: Path,
    *arguments: str,
    input_bytes: bytes | None = None,
) -> bytes:
    completed = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        input=input_bytes,
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise P0PerformanceError(f"Git plumbing failed for {arguments!r}: {detail}")
    return completed.stdout


def _git_text(repository: Path, *arguments: str) -> str:
    return _git(repository, *arguments).decode("utf-8", errors="strict").rstrip("\n")


def git_blob_oid(repository: Path, payload: bytes) -> str:
    return (
        _git(repository, "hash-object", "--stdin", input_bytes=payload)
        .decode("ascii", errors="strict")
        .strip()
    )


def _commit_parents(repository: Path, commit: str) -> list[str]:
    parts = _git_text(repository, "rev-list", "--parents", "-n", "1", commit).split()
    if not parts or parts[0] != commit:
        raise P0PerformanceError(f"Git object is not the expected commit: {commit}")
    return parts[1:]


def _ls_tree_record(repository: Path, commit: str, path: str) -> dict[str, str]:
    path = _validate_repository_path(path, "Git tree")
    output = _git(repository, "ls-tree", "-z", commit, "--", f":(literal){path}")
    records = [item for item in output.split(b"\0") if item]
    if len(records) != 1:
        raise P0PerformanceError(f"Git tree path is not unique: {path}")
    try:
        header, raw_path = records[0].split(b"\t", 1)
        mode, object_type, oid = header.decode("ascii").split()
        decoded_path = raw_path.decode("utf-8", errors="strict")
    except (UnicodeError, ValueError) as exc:
        raise P0PerformanceError("invalid NUL-safe ls-tree record") from exc
    if decoded_path != path:
        raise P0PerformanceError("NUL-safe ls-tree path differs from literal request")
    return {"mode": mode, "type": object_type, "oid": oid, "path": decoded_path}


def _tree_path_exists(repository: Path, commit: str, path: str) -> bool:
    path = _validate_repository_path(path, "Git tree")
    return bool(_git(repository, "ls-tree", "-z", commit, "--", f":(literal){path}"))


def _diff_tree_records(
    repository: Path, parent: str, child: str
) -> list[dict[str, Any]]:
    output = _git(
        repository,
        "diff-tree",
        "--raw",
        "-r",
        "-z",
        "--no-commit-id",
        parent,
        child,
    )
    fields = output.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    records: list[dict[str, Any]] = []
    index = 0
    while index < len(fields):
        header = fields[index]
        index += 1
        if not header.startswith(b":"):
            raise P0PerformanceError("invalid NUL-safe diff-tree header")
        try:
            old_mode, new_mode, old_oid, new_oid, status = (
                header[1:].decode("ascii").split()
            )
        except (UnicodeError, ValueError) as exc:
            raise P0PerformanceError("invalid raw diff metadata") from exc
        path_count = 2 if status.startswith(("R", "C")) else 1
        if index + path_count > len(fields):
            raise P0PerformanceError("truncated NUL-safe diff-tree paths")
        try:
            paths = [
                fields[index + offset].decode("utf-8", errors="strict")
                for offset in range(path_count)
            ]
        except UnicodeError as exc:
            raise P0PerformanceError(
                "non-UTF-8 Git path in authorization delta"
            ) from exc
        index += path_count
        records.append(
            {
                "old_mode": old_mode,
                "new_mode": new_mode,
                "old_oid": old_oid,
                "new_oid": new_oid,
                "status": status,
                "paths": paths,
            }
        )
    return records


def _blob_record(
    repository: Path,
    record: Mapping[str, Any],
    *,
    payload: bytes,
    observed_oid: str,
    observed_mode: str,
    label: str,
) -> None:
    _exact_keys(record, _BLOB_RECORD_FIELDS, label)
    object_format = _git_text(repository, "rev-parse", "--show-object-format")
    expected = {
        "git_oid": observed_oid,
        "git_object_algorithm": object_format,
        "content_sha256": hashlib.sha256(payload).hexdigest(),
        "byte_length": len(payload),
        "encoding": "UTF-8",
        "line_endings": "LF",
        "mode": observed_mode,
    }
    if dict(record) != expected:
        raise P0PerformanceError(f"{label} does not match observed Git blob bytes")
    strict_json_bytes(payload, label=label)


def _bound_file(
    repository: Path,
    commit: str,
    record: Mapping[str, Any],
    *,
    label: str,
) -> None:
    _exact_keys(record, _BOUND_FILE_FIELDS, label)
    path = _validate_repository_path(record.get("path"), label)
    tree = _ls_tree_record(repository, commit, path)
    if tree != {
        "mode": record.get("mode"),
        "type": "blob",
        "oid": record.get("git_blob"),
        "path": path,
    }:
        raise P0PerformanceError(f"{label} tree binding failed")
    payload = _git(repository, "cat-file", "blob", tree["oid"])
    if not (
        hashlib.sha256(payload).hexdigest() == record.get("sha256")
        and len(payload) == record.get("byte_length")
    ):
        raise P0PerformanceError(f"{label} content binding failed")


def _validate_production_static_audit(
    audit: Mapping[str, Any],
    *,
    capability_manifest: Mapping[str, Any],
) -> None:
    required_gates = {
        "actual_parent_ticket_absent",
        "authorization_default_denial",
        "contract_generated_outputs_and_quality_bound",
        "formal_source_unchanged",
        "hf3d_time_steps_zero",
        "instrumentation_numerical_equivalence",
        "native_allocator_build_and_probes",
        "nsys_minimal_trace_and_parsers",
        "ordered_v2_zero_step_preflight_static_pass_default_denied",
        "preclaim_machine_identity_rebuild_exact",
        "p0d_and_pro_evidence_bound",
        "p1_not_implemented",
        "performance_samples_started_zero",
        "ruff_mypy_and_python_compile",
        "runtime_raw_lock_claim_ledger_sample_absent",
        "self_referential_authorization_fields_absent",
        "synthetic_96c_environment_rejected",
        "synthetic_git_topology_transaction_and_harness_tests",
        "toolchain_capability_manifest_bound",
        "worker_denies_before_numerical_imports",
    }
    gates = audit.get("gates")
    counters = audit.get("counters")
    authorization = audit.get("authorization")
    before = audit.get("formal_source_before")
    after = audit.get("formal_source_after")
    if not (
        audit.get("status")
        == "HF3D_P0P2_V2_ZERO_STEP_HARNESS_STATIC_AUDIT_PASS"
        and audit.get("gate_pass") is True
        and isinstance(gates, Mapping)
        and required_gates <= set(gates)
        and all(gates.get(name) is True for name in required_gates)
        and counters
        == {
            "hf3d_time_steps_advanced": 0,
            "warmup_samples_started": 0,
            "scored_samples_started": 0,
            "profile_samples_started": 0,
            "performance_samples_started": 0,
            "p0p2v2_claims_consumed": 0,
        }
        and audit.get("p0p2v2_dynamic_authorization") == "NOT_AUTHORIZED"
        and audit.get("p1_implemented") is False
        and isinstance(authorization, Mapping)
        and isinstance(before, Mapping)
        and isinstance(after, Mapping)
        and before == after
        and before.get("sha256") == FORMAL_SOURCE_SHA256
        and audit.get("capability_manifest") == dict(capability_manifest)
    ):
        raise P0PerformanceError("reviewed production static audit hard gates failed")
    validate_denied_authorization(authorization)


def validate_ticket(ticket: Mapping[str, Any]) -> dict[str, Any]:
    _exact_keys(ticket, TICKET_FIELDS, "authorization ticket")
    if ticket.get("schema_version") != 2 or ticket.get("status") != TICKET_STATUS:
        raise P0PerformanceError("authorization ticket status/schema is invalid")
    if ticket.get("contract_id") != CONTRACT_ID:
        raise P0PerformanceError("authorization ticket contract identity is invalid")
    if (
        not isinstance(ticket.get("ticket_id"), str)
        or not ticket["ticket_id"].strip()
        or "\n" in ticket["ticket_id"]
    ):
        raise P0PerformanceError("authorization ticket ID is invalid")
    if ticket.get("authorization_path") != AUTHORIZATION_RELATIVE_PATH:
        raise P0PerformanceError("authorization ticket path is invalid")
    _validate_repository_path(ticket.get("ruling_path"), "authorization ruling")
    if ticket.get("fixed_schedule") != fixed_schedule_manifest():
        raise P0PerformanceError("authorization ticket schedule differs from frozen 18")
    reviewed = ticket.get("reviewed_snapshot")
    if not isinstance(reviewed, Mapping):
        raise P0PerformanceError("ticket reviewed_snapshot is missing")
    _exact_keys(
        reviewed,
        frozenset(
            {
                "commit_id",
                "tree_id",
                "static_audit",
                "implementation_files",
                "contract",
                "p0_d_evidence",
                "capability_manifest",
            }
        ),
        "reviewed_snapshot",
    )
    if not (
        isinstance(reviewed.get("commit_id"), str)
        and _GIT_OID.fullmatch(str(reviewed["commit_id"]))
        and isinstance(reviewed.get("tree_id"), str)
        and _GIT_OID.fullmatch(str(reviewed["tree_id"]))
    ):
        raise P0PerformanceError("reviewed snapshot commit/tree identity is invalid")
    if (
        not isinstance(reviewed.get("implementation_files"), list)
        or not reviewed["implementation_files"]
    ):
        raise P0PerformanceError("ticket implementation binding is empty")
    if (
        not isinstance(reviewed.get("p0_d_evidence"), list)
        or not reviewed["p0_d_evidence"]
    ):
        raise P0PerformanceError("ticket P0-D binding is empty")
    policy = ticket.get("review_parent_policy")
    if not isinstance(policy, Mapping):
        raise P0PerformanceError("ticket review-parent policy is missing")
    _exact_keys(
        policy,
        frozenset(
            {
                "allowed_review_paths",
                "authorization_path_unchanged",
                "protected_prefixes",
                "ticket_path",
                "ruling_path",
                "current_path",
                "history_path",
                "metadata_mode",
            }
        ),
        "review_parent_policy",
    )
    allowed = policy.get("allowed_review_paths")
    if (
        not isinstance(allowed, list)
        or not allowed
        or any(not isinstance(path, str) or not path for path in allowed)
    ):
        raise P0PerformanceError("review-only path set is invalid")
    if len(set(allowed)) != len(allowed):
        raise P0PerformanceError("review-only path set contains duplicates")
    for index, path in enumerate(allowed):
        _validate_repository_path(path, f"review-only path {index}")
    protected_prefixes = policy.get("protected_prefixes")
    if (
        not isinstance(protected_prefixes, list)
        or not protected_prefixes
        or any(not isinstance(value, str) or not value for value in protected_prefixes)
    ):
        raise P0PerformanceError("protected review-prefix set is invalid")
    if not (
        policy.get("authorization_path_unchanged") is True
        and policy.get("ticket_path") == TICKET_RELATIVE_PATH
        and policy.get("ruling_path") == ticket["ruling_path"]
        and policy.get("current_path") == CURRENT_RELATIVE_PATH
        and policy.get("history_path") == HISTORY_RELATIVE_PATH
        and policy.get("metadata_mode") == "100644"
    ):
        raise P0PerformanceError("review-parent policy identity failed")
    required_review_paths = {
        TICKET_RELATIVE_PATH,
        str(ticket["ruling_path"]),
        CURRENT_RELATIVE_PATH,
        HISTORY_RELATIVE_PATH,
    }
    if set(allowed) != required_review_paths:
        raise P0PerformanceError(
            "review parent path set must be exactly ticket/ruling/current/history"
        )
    child = ticket.get("child_commit_policy")
    claim = ticket.get("claim_policy")
    if child != {
        "single_parent": True,
        "exactly_one_modified_path": AUTHORIZATION_RELATIVE_PATH,
        "old_mode": "100644",
        "new_mode": "100644",
        "rename_copy_symlink_submodule": "PROHIBITED",
    }:
        raise P0PerformanceError("child commit policy is invalid")
    if claim != {
        "maximum_schedule_claims": 1,
        "preflight_consumes_claim": False,
        "claim_before_first_sample": True,
        "sample_replacement": "PROHIBITED",
        "schedule_resume_after_claim": "PROHIBITED",
        "schedule_retry_or_supplement": "PROHIBITED",
    }:
        raise P0PerformanceError("claim policy is invalid")
    if (
        not isinstance(ticket.get("toolchain_binding"), Mapping)
        or not ticket["toolchain_binding"]
    ):
        raise P0PerformanceError("toolchain binding is empty")
    if not (
        isinstance(ticket.get("formal_source_tree_sha256"), str)
        and _SHA256.fullmatch(str(ticket["formal_source_tree_sha256"]))
    ):
        raise P0PerformanceError("formal source binding is invalid")
    for name in ("denied_parent_blob", "authorized_child_blob"):
        value = ticket.get(name)
        if not isinstance(value, Mapping):
            raise P0PerformanceError(f"ticket {name} is missing")
        _exact_keys(value, _BLOB_RECORD_FIELDS, name)
        if not (
            _GIT_OID.fullmatch(str(value.get("git_oid", "")))
            and _SHA256.fullmatch(str(value.get("content_sha256", "")))
            and type(value.get("byte_length")) is int
            and int(value["byte_length"]) > 0
            and value.get("mode") == "100644"
            and value.get("encoding") == "UTF-8"
            and value.get("line_endings") == "LF"
        ):
            raise P0PerformanceError(f"ticket {name} identity is invalid")
    return dict(ticket)


def audit_authorized_repository(
    repository: Path,
    authorization: Mapping[str, Any],
    *,
    require_production_bindings: bool = True,
) -> dict[str, Any]:
    """Audit I -> P -> C using only NUL-safe Git plumbing and exact blobs."""

    repository = repository.resolve()
    grant = validate_authorized_grant(authorization)
    _git(repository, "fetch", "--no-tags", "origin", "main")
    branch = _git_text(repository, "symbolic-ref", "--quiet", "HEAD")
    child = _git_text(repository, "rev-parse", "HEAD")
    origin_main = _git_text(repository, "rev-parse", "refs/remotes/origin/main")
    if branch != "refs/heads/main" or child != origin_main:
        raise P0PerformanceError(
            "local main and origin/main authorization heads differ"
        )
    status = _git(repository, "status", "--porcelain=v2", "-z", "--untracked-files=all")
    if status:
        raise P0PerformanceError("authorized execution requires a clean worktree/index")
    child_parents = _commit_parents(repository, child)
    if len(child_parents) != 1:
        raise P0PerformanceError("authorization child must have exactly one parent")
    parent = child_parents[0]
    ticket_bytes = _git(
        repository, "cat-file", "blob", f"{parent}:{TICKET_RELATIVE_PATH}"
    )
    ticket = validate_ticket(
        strict_json_bytes(ticket_bytes, label=f"{parent}:{TICKET_RELATIVE_PATH}")
    )
    reviewed = ticket["reviewed_snapshot"]
    implementation = str(reviewed["commit_id"])
    if grant["reviewed_snapshot_commit"] != implementation:
        raise P0PerformanceError("grant reviewed snapshot differs from ticket")
    parent_parents = _commit_parents(repository, parent)
    if parent_parents != [implementation]:
        raise P0PerformanceError("review parent is not the direct child of snapshot I")
    if (
        _git_text(repository, "rev-parse", f"{implementation}^{{tree}}")
        != reviewed["tree_id"]
    ):
        raise P0PerformanceError("reviewed snapshot tree identity failed")
    review_delta = _diff_tree_records(repository, implementation, parent)
    review_paths = [path for record in review_delta for path in record["paths"]]
    policy = ticket["review_parent_policy"]
    if sorted(review_paths) != sorted(policy["allowed_review_paths"]):
        raise P0PerformanceError("I..P review-only path set differs from ticket")
    zero_oid_lengths = {40, 64}

    def valid_review_record(record: Mapping[str, Any]) -> bool:
        status = record["status"]
        old_oid = str(record["old_oid"])
        new_oid = str(record["new_oid"])
        shared = (
            len(record["paths"]) == 1
            and record["new_mode"] == "100644"
            and _GIT_OID.fullmatch(new_oid) is not None
        )
        if status == "A":
            return (
                shared
                and record["old_mode"] == "000000"
                and len(old_oid) in zero_oid_lengths
                and set(old_oid) == {"0"}
            )
        if status == "M":
            return (
                shared
                and record["old_mode"] == "100644"
                and _GIT_OID.fullmatch(old_oid) is not None
                and old_oid != new_oid
            )
        return False

    if any(not valid_review_record(record) for record in review_delta):
        raise P0PerformanceError(
            "I..P review delta contains deletion/rename/copy/non-regular mode"
        )
    for path in review_paths:
        tree = _ls_tree_record(repository, parent, path)
        if tree["mode"] != "100644" or tree["type"] != "blob":
            raise P0PerformanceError("I..P review output is not a regular blob")
    protected = tuple(str(value) for value in policy["protected_prefixes"])
    if any(
        path.startswith((*protected, *_ALWAYS_PROTECTED_REVIEW_PREFIXES))
        for path in review_paths
    ):
        raise P0PerformanceError("I..P changed a protected prefix")
    immutable_records = [
        reviewed["static_audit"],
        reviewed["contract"],
        reviewed["capability_manifest"],
        *reviewed["implementation_files"],
        *reviewed["p0_d_evidence"],
    ]
    immutable_paths = {
        AUTHORIZATION_RELATIVE_PATH,
        *(
            str(record["path"])
            for record in immutable_records
            if isinstance(record, Mapping) and isinstance(record.get("path"), str)
        ),
    }
    if immutable_paths & set(review_paths):
        raise P0PerformanceError("I..P changed a reviewed immutable file")
    if not {
        TICKET_RELATIVE_PATH,
        str(ticket["ruling_path"]),
    } <= set(review_paths):
        raise P0PerformanceError("I..P lacks the ticket or ruling review path")
    ticket_deltas = [
        record for record in review_delta if record["paths"] == [TICKET_RELATIVE_PATH]
    ]
    if (
        len(ticket_deltas) != 1
        or ticket_deltas[0]["status"] != "A"
        or _tree_path_exists(repository, implementation, TICKET_RELATIVE_PATH)
    ):
        raise P0PerformanceError("parent ticket was not created exactly once in P")
    child_delta = _diff_tree_records(repository, parent, child)
    if len(child_delta) != 1:
        raise P0PerformanceError("P..C must contain exactly one raw diff record")
    delta = child_delta[0]
    if not (
        delta["status"] == "M"
        and delta["paths"] == [AUTHORIZATION_RELATIVE_PATH]
        and delta["old_mode"] == delta["new_mode"] == "100644"
    ):
        raise P0PerformanceError("P..C is not the exact regular auth-file modification")
    parent_tree = _ls_tree_record(repository, parent, AUTHORIZATION_RELATIVE_PATH)
    child_tree = _ls_tree_record(repository, child, AUTHORIZATION_RELATIVE_PATH)
    implementation_auth_tree = _ls_tree_record(
        repository, implementation, AUTHORIZATION_RELATIVE_PATH
    )
    if not (
        parent_tree["oid"] == delta["old_oid"]
        and implementation_auth_tree == parent_tree
        and child_tree["oid"] == delta["new_oid"]
        and parent_tree["mode"] == child_tree["mode"] == "100644"
        and parent_tree["type"] == child_tree["type"] == "blob"
    ):
        raise P0PerformanceError("authorization diff and tree identities disagree")
    denied_bytes = _git(
        repository, "cat-file", "blob", f"{parent}:{AUTHORIZATION_RELATIVE_PATH}"
    )
    authorized_bytes = _git(
        repository, "cat-file", "blob", f"{child}:{AUTHORIZATION_RELATIVE_PATH}"
    )
    validate_denied_authorization(
        strict_json_bytes(denied_bytes, label="parent denied authorization")
    )
    if strict_json_bytes(authorized_bytes, label="child authorized grant") != dict(
        grant
    ):
        raise P0PerformanceError("worktree grant differs from child Git object")
    _blob_record(
        repository,
        ticket["denied_parent_blob"],
        payload=denied_bytes,
        observed_oid=parent_tree["oid"],
        observed_mode=parent_tree["mode"],
        label="denied_parent_blob",
    )
    _blob_record(
        repository,
        ticket["authorized_child_blob"],
        payload=authorized_bytes,
        observed_oid=child_tree["oid"],
        observed_mode=child_tree["mode"],
        label="authorized_child_blob",
    )
    ticket_tree = _ls_tree_record(repository, parent, TICKET_RELATIVE_PATH)
    ruling_tree = _ls_tree_record(repository, parent, str(ticket["ruling_path"]))
    if not (
        ticket_tree["mode"] == ruling_tree["mode"] == "100644"
        and ticket_tree["type"] == ruling_tree["type"] == "blob"
    ):
        raise P0PerformanceError("parent ticket/ruling must be regular 100644 blobs")
    for label in ("static_audit", "contract", "capability_manifest"):
        record = reviewed[label]
        if not isinstance(record, Mapping):
            raise P0PerformanceError(f"reviewed {label} binding is missing")
        _bound_file(repository, implementation, record, label=label)
    for index, record in enumerate(reviewed["implementation_files"]):
        if not isinstance(record, Mapping):
            raise P0PerformanceError("implementation file binding is invalid")
        _bound_file(
            repository,
            implementation,
            record,
            label=f"implementation_files[{index}]",
        )
    for index, record in enumerate(reviewed["p0_d_evidence"]):
        if not isinstance(record, Mapping):
            raise P0PerformanceError("P0-D file binding is invalid")
        _bound_file(
            repository,
            implementation,
            record,
            label=f"p0_d_evidence[{index}]",
        )
    capability_record = reviewed["capability_manifest"]
    capability_tree = _ls_tree_record(
        repository, implementation, str(capability_record["path"])
    )
    capability_payload = strict_json_bytes(
        _git(repository, "cat-file", "blob", capability_tree["oid"]),
        label="reviewed capability manifest",
    )
    if capability_payload != dict(ticket["toolchain_binding"]):
        raise P0PerformanceError(
            "ticket toolchain binding differs from reviewed capability manifest"
        )
    if not (
        grant["ticket_id"] == ticket["ticket_id"]
        and grant["fixed_schedule_id"] == ticket["fixed_schedule"]["schedule_id"]
        and grant["fixed_schedule_sha256"]
        == ticket["fixed_schedule"]["canonical_schedule_sha256"]
    ):
        raise P0PerformanceError("grant and parent ticket identity differ")
    if require_production_bindings:
        if repository != REPOSITORY_ROOT.resolve():
            raise P0PerformanceError(
                "production binding requested for another repository"
            )
        if ticket["formal_source_tree_sha256"] != FORMAL_SOURCE_SHA256:
            raise P0PerformanceError(
                "ticket formal source tree differs from production"
            )
        static_record = reviewed["static_audit"]
        static_payload = strict_json_bytes(
            _git(
                repository,
                "cat-file",
                "blob",
                f"{implementation}:{static_record['path']}",
            ),
            label="ticket production static audit",
        )
        _validate_production_static_audit(
            static_payload,
            capability_manifest=capability_payload,
        )
        implementation_paths = [
            str(record.get("path"))
            for record in reviewed["implementation_files"]
            if isinstance(record, Mapping)
        ]
        if len(implementation_paths) != len(set(implementation_paths)) or set(
            implementation_paths
        ) != set(REQUIRED_IMPLEMENTATION_RELATIVE_PATHS):
            raise P0PerformanceError(
                "ticket implementation manifest is not the complete frozen harness"
            )
        contract_record = reviewed["contract"]
        if not (
            contract_record["path"].endswith("p0p2v2_contract.json")
            and strict_json_bytes(
                _git(
                    repository,
                    "cat-file",
                    "blob",
                    f"{implementation}:{contract_record['path']}",
                ),
                label="ticket contract",
            ).get("contract_id")
            == CONTRACT_ID
        ):
            raise P0PerformanceError("ticket P0 contract identity failed")
    return {
        "gate_pass": True,
        "authorization_child_commit": child,
        "review_parent_commit": parent,
        "reviewed_snapshot_commit": implementation,
        "ticket": ticket,
        "ticket_blob": ticket_tree["oid"],
        "ticket_sha256": hashlib.sha256(ticket_bytes).hexdigest(),
        "denied_parent_blob": parent_tree["oid"],
        "authorized_child_blob": child_tree["oid"],
        "fixed_schedule": ticket["fixed_schedule"],
        "git_object_algorithm": _git_text(
            repository, "rev-parse", "--show-object-format"
        ),
        "nul_safe_review_delta": review_delta,
        "nul_safe_authorization_delta": child_delta,
    }


def load_authorization(*, require_dynamic: bool) -> dict[str, Any]:
    authorization = strict_json(AUTHORIZATION_PATH)
    authorized = (
        authorization.get("status") == AUTHORIZED_STATUS
        and authorization.get("dynamic_samples_authorized") is True
    )
    if not authorized:
        denied = validate_denied_authorization(authorization)
        if denied != default_denied_authorization():
            raise P0PerformanceError(
                "P0-P2 V2 authorization differs from the exact default denial"
            )
        if not require_dynamic:
            return denied
        raise P0PerformanceError(
            "HF3D_P0P2_V2_DYNAMIC_SAMPLES_NOT_AUTHORIZED_PENDING_PRO_REVIEW"
        )
    validate_authorized_grant(authorization)
    binding = audit_authorized_repository(
        REPOSITORY_ROOT,
        authorization,
        require_production_bindings=True,
    )
    return {**authorization, "observed_parent_ticket_binding": binding}


__all__ = [
    "AUTHORIZATION_RELATIVE_PATH",
    "AUTHORIZED_FIELDS",
    "AUTHORIZED_STATUS",
    "CURRENT_RELATIVE_PATH",
    "DENIED_FIELDS",
    "FIXED_SCHEDULE_ID",
    "FORBIDDEN_SELF_FIELDS",
    "HISTORY_RELATIVE_PATH",
    "REQUIRED_IMPLEMENTATION_RELATIVE_PATHS",
    "TICKET_FIELDS",
    "TICKET_PATH",
    "TICKET_RELATIVE_PATH",
    "TICKET_STATUS",
    "audit_authorized_repository",
    "default_denied_authorization",
    "fixed_schedule_manifest",
    "git_blob_oid",
    "load_authorization",
    "validate_authorized_grant",
    "validate_denied_authorization",
    "validate_ticket",
]
