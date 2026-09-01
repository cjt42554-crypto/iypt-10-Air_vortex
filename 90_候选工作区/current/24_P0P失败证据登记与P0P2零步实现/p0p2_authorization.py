"""Default denial and future exact I2 -> P2 -> C2 authorization audit."""

from __future__ import annotations

import hashlib
import re
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from p0p2_contract import (
    AUTHORIZATION_ID,
    AUTHORIZATION_PATH,
    AUTHORIZED_STATUS,
    CONTRACT_ID,
    CONTRACT_PATH,
    DENIED_STATUS,
    E0_AUDIT_PATH,
    E0_CANONICAL_TREE_SHA256,
    E0_MANIFEST_PATH,
    E0_SNAPSHOT_ROOT,
    EFFECTIVE_CONFIG_ROOT,
    FORMAL_SOURCE_SHA256,
    GRANT_NONCE_PREFIX,
    HERE,
    REPOSITORY_ROOT,
    SCHEDULE_ID,
    STATIC_ROOT,
    TICKET_PATH,
    P0P2Error,
    default_authorization,
    fixed_schedule,
    relative,
    source_config_path,
    strict_json,
    strict_json_bytes,
)

TICKET_STATUS = "READY_FOR_SINGLE_P0P2_AUTHORIZATION_CHILD"
_HEX40 = re.compile(r"[0-9a-f]{40}")
_HEX64 = re.compile(r"[0-9a-f]{64}")

_IMPLEMENTATION_FILENAMES = (
    ".gitignore",
    "README.md",
    "audit_p0p2_zero_step.py",
    "build_p0p2_contract.py",
    "p0p2_authorization.py",
    "p0p2_contract.py",
    "p0p2_environment.py",
    "p0p2_zero_step_probe.py",
    "register_v1_failure_evidence.py",
    "run_p0p2_preflight.py",
)
_TEST_FILENAMES = (
    "test_p0p2_authorization.py",
    "test_p0p2_contract_and_preflight.py",
    "test_p0p2_environment.py",
    "test_v1_failure_evidence_registration.py",
)
_REQUIRED_PROTECTED_PREFIXES = {
    "01_最终理论/",
    "02_模拟程序/",
    "90_候选工作区/current/07_D3跨网格时间步响应诊断/",
    "90_候选工作区/current/17_HF3D性能优化/",
    "90_候选工作区/current/23_P0P一次性执行失败独立复核与P0P2协议裁决.md",
    "AGENTS.md",
}

AUTHORIZED_FIELDS = frozenset(
    {
        "schema_version",
        "status",
        "authorization_id",
        "contract_id",
        "dynamic_samples_authorized",
        "reviewed_snapshot_commit",
        "ticket_id",
        "ticket_path",
        "fixed_schedule_id",
        "fixed_schedule_sha256",
        "grant_nonce",
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
        "status",
        "ticket_id",
        "authorization_path",
        "ruling_path",
        "reviewed_snapshot",
        "fixed_schedule",
        "formal_source_tree_sha256",
        "e0_canonical_tree_sha256",
        "denied_parent_blob",
        "authorized_child_blob",
        "review_parent_policy",
        "child_commit_policy",
        "claim_policy",
    }
)


def _exact_keys(value: Mapping[str, Any], expected: frozenset[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        raise P0P2Error(
            f"{label} keys differ: missing={sorted(expected - actual)}, "
            f"extra={sorted(actual - expected)}"
        )


def validate_denied_authorization(value: Mapping[str, Any]) -> dict[str, Any]:
    expected = default_authorization()
    if dict(value) != expected:
        raise P0P2Error("P0-P2 authorization is not the exact frozen denial")
    return {
        **expected,
        "authorization_gate_pass": False,
        "denial_schema_valid": True,
        "dynamic_execution_allowed": False,
    }


def validate_authorized_grant(value: Mapping[str, Any]) -> dict[str, Any]:
    _exact_keys(value, AUTHORIZED_FIELDS, "authorized grant")
    schedule = fixed_schedule()
    gates = {
        "schema_version": value.get("schema_version") == 2,
        "status": value.get("status") == AUTHORIZED_STATUS,
        "authorization_id": value.get("authorization_id") == AUTHORIZATION_ID,
        "contract_id": value.get("contract_id") == CONTRACT_ID,
        "dynamic_samples_authorized": value.get("dynamic_samples_authorized") is True,
        "reviewed_snapshot_commit": isinstance(
            value.get("reviewed_snapshot_commit"), str
        )
        and bool(_HEX40.fullmatch(str(value["reviewed_snapshot_commit"]))),
        "ticket_id": isinstance(value.get("ticket_id"), str)
        and value["ticket_id"].startswith("HF3D_P0P2_PRO_TICKET_")
        and "\n" not in value["ticket_id"],
        "ticket_path": value.get("ticket_path") == relative(TICKET_PATH),
        "fixed_schedule_id": value.get("fixed_schedule_id") == SCHEDULE_ID,
        "fixed_schedule_sha256": value.get("fixed_schedule_sha256")
        == schedule["canonical_schedule_sha256"],
        "grant_nonce": isinstance(value.get("grant_nonce"), str)
        and str(value["grant_nonce"]).startswith(GRANT_NONCE_PREFIX)
        and bool(
            _HEX64.fullmatch(str(value["grant_nonce"])[len(GRANT_NONCE_PREFIX) :])
        ),
        "maximum_schedule_claims": value.get("maximum_schedule_claims") == 1,
        "preflight_consumes_claim": value.get("preflight_consumes_claim") is False,
        "claim_before_first_sample": value.get("claim_before_first_sample") is True,
        "sample_replacement": value.get("sample_replacement") == "PROHIBITED",
        "schedule_resume_after_claim": value.get("schedule_resume_after_claim")
        == "PROHIBITED",
        "schedule_retry_or_supplement": value.get("schedule_retry_or_supplement")
        == "PROHIBITED",
        "P1": value.get("P1") == "PROHIBITED",
    }
    if not all(gates.values()):
        failed = [name for name, passed in gates.items() if not passed]
        raise P0P2Error("authorized grant fields failed: " + ", ".join(failed))
    return dict(value)


def _git_bytes(repository: Path, *arguments: str) -> bytes:
    completed = subprocess.run(
        ["git", "-c", "core.quotePath=false", *arguments],
        cwd=repository,
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise P0P2Error(f"Git authorization audit failed: {detail}")
    return completed.stdout


def _git_text(repository: Path, *arguments: str) -> str:
    return _git_bytes(repository, *arguments).decode("utf-8", errors="strict").strip()


def _nul_paths(repository: Path, start: str, end: str) -> list[str]:
    payload = _git_bytes(
        repository,
        "diff",
        "--name-only",
        "--no-renames",
        "-z",
        start,
        end,
    )
    return [
        item.decode("utf-8", errors="strict") for item in payload.split(b"\0") if item
    ]


def _tree_record(repository: Path, commit: str, path: str) -> dict[str, str]:
    payload = _git_bytes(repository, "ls-tree", "-z", commit, "--", path)
    records = [item for item in payload.split(b"\0") if item]
    if len(records) != 1:
        raise P0P2Error(f"expected one Git tree record for {commit}:{path}")
    metadata, observed_path = records[0].split(b"\t", 1)
    mode, object_type, oid = metadata.decode("ascii").split()
    decoded = observed_path.decode("utf-8", errors="strict")
    if decoded != path:
        raise P0P2Error(f"Git tree path identity changed: {path}")
    return {"mode": mode, "type": object_type, "oid": oid, "path": decoded}


def _validate_blob_record(
    repository: Path,
    commit: str,
    record: Mapping[str, Any],
    *,
    label: str,
) -> None:
    expected_keys = {"path", "mode", "git_blob", "sha256", "byte_length"}
    if set(record) != expected_keys:
        raise P0P2Error(f"{label} binding keys differ")
    path = record.get("path")
    if (
        not isinstance(path, str)
        or not path
        or path.startswith("/")
        or ".." in Path(path).parts
    ):
        raise P0P2Error(f"{label} path is invalid")
    tree = _tree_record(repository, commit, path)
    payload = _git_bytes(repository, "cat-file", "blob", tree["oid"])
    if not (
        tree["mode"] == record.get("mode")
        and tree["type"] == "blob"
        and tree["oid"] == record.get("git_blob")
        and len(payload) == record.get("byte_length")
        and hashlib.sha256(payload).hexdigest() == record.get("sha256")
    ):
        raise P0P2Error(f"{label} Git blob identity failed")


def validate_ticket(ticket: Mapping[str, Any]) -> dict[str, Any]:
    _exact_keys(ticket, TICKET_FIELDS, "P0-P2 ticket")
    if not (
        ticket.get("schema_version") == 1
        and ticket.get("status") == TICKET_STATUS
        and isinstance(ticket.get("ticket_id"), str)
        and ticket["ticket_id"].startswith("HF3D_P0P2_PRO_TICKET_")
        and ticket.get("authorization_path") == relative(AUTHORIZATION_PATH)
        and ticket.get("fixed_schedule") == fixed_schedule()
        and ticket.get("formal_source_tree_sha256") == FORMAL_SOURCE_SHA256
        and ticket.get("e0_canonical_tree_sha256") == E0_CANONICAL_TREE_SHA256
    ):
        raise P0P2Error("P0-P2 ticket fixed identity failed")
    reviewed = ticket.get("reviewed_snapshot")
    if not isinstance(reviewed, Mapping):
        raise P0P2Error("P0-P2 ticket reviewed snapshot is missing")
    if set(reviewed) != {"commit", "tree", "files"}:
        raise P0P2Error("P0-P2 reviewed snapshot fields differ")
    if not (
        isinstance(reviewed.get("commit"), str)
        and _HEX40.fullmatch(str(reviewed["commit"]))
        and isinstance(reviewed.get("tree"), str)
        and _HEX40.fullmatch(str(reviewed["tree"]))
        and isinstance(reviewed.get("files"), list)
        and reviewed["files"]
    ):
        raise P0P2Error("P0-P2 reviewed snapshot identity is invalid")
    policy = ticket.get("review_parent_policy")
    child = ticket.get("child_commit_policy")
    claim = ticket.get("claim_policy")
    if not (
        isinstance(policy, Mapping)
        and set(policy)
        == {"allowed_review_paths", "protected_prefixes", "ticket_path", "ruling_path"}
        and isinstance(policy.get("allowed_review_paths"), list)
        and policy.get("ticket_path") == relative(TICKET_PATH)
        and policy.get("ruling_path") == ticket.get("ruling_path")
        and isinstance(policy.get("protected_prefixes"), list)
        and isinstance(child, Mapping)
        and dict(child)
        == {
            "single_parent": True,
            "exactly_one_modified_path": relative(AUTHORIZATION_PATH),
            "old_mode": "100644",
            "new_mode": "100644",
            "rename_copy_symlink_submodule": "PROHIBITED",
        }
        and isinstance(claim, Mapping)
        and dict(claim)
        == {
            "maximum_schedule_claims": 1,
            "preflight_consumes_claim": False,
            "claim_before_first_sample": True,
            "sample_replacement": "PROHIBITED",
            "schedule_resume_after_claim": "PROHIBITED",
            "schedule_retry_or_supplement": "PROHIBITED",
            "P1": "PROHIBITED",
        }
    ):
        raise P0P2Error("P0-P2 ticket policy is invalid")
    assert isinstance(policy, Mapping)
    allowed_paths = policy["allowed_review_paths"]
    protected_prefixes = policy["protected_prefixes"]
    ruling_path = ticket.get("ruling_path")
    if not (
        isinstance(ruling_path, str)
        and ruling_path
        and not ruling_path.startswith("/")
        and ".." not in Path(ruling_path).parts
        and isinstance(allowed_paths, list)
        and all(
            isinstance(path, str)
            and path
            and not path.startswith("/")
            and ".." not in Path(path).parts
            for path in allowed_paths
        )
        and len(allowed_paths) == len(set(allowed_paths))
        and {relative(TICKET_PATH), ruling_path} <= set(allowed_paths)
        and isinstance(protected_prefixes, list)
        and all(isinstance(prefix, str) for prefix in protected_prefixes)
        and _REQUIRED_PROTECTED_PREFIXES <= set(protected_prefixes)
    ):
        raise P0P2Error("P0-P2 ticket review path policy is incomplete")
    allowed_review_metadata = {
        relative(TICKET_PATH),
        ruling_path,
        relative(HERE.parents[1] / "交接说明_current.md"),
        relative(HERE.parents[1] / "交接说明_history.md"),
    }
    if not set(allowed_paths) <= allowed_review_metadata:
        raise P0P2Error("P0-P2 ticket permits a non-review-metadata path")
    for label in ("denied_parent_blob", "authorized_child_blob"):
        record = ticket.get(label)
        if not (
            isinstance(record, Mapping)
            and set(record) == {"git_oid", "sha256", "byte_length", "mode"}
            and record.get("mode") == "100644"
            and isinstance(record.get("git_oid"), str)
            and _HEX40.fullmatch(str(record["git_oid"]))
            and isinstance(record.get("sha256"), str)
            and _HEX64.fullmatch(str(record["sha256"]))
            and isinstance(record.get("byte_length"), int)
            and int(record["byte_length"]) > 0
        ):
            raise P0P2Error(f"P0-P2 ticket {label} identity is invalid")
    return dict(ticket)


def _validate_exact_blob(
    payload: bytes,
    tree: Mapping[str, str],
    expected: Mapping[str, Any],
    *,
    label: str,
) -> None:
    if not (
        tree.get("mode") == expected.get("mode") == "100644"
        and tree.get("type") == "blob"
        and tree.get("oid") == expected.get("git_oid")
        and len(payload) == expected.get("byte_length")
        and hashlib.sha256(payload).hexdigest() == expected.get("sha256")
    ):
        raise P0P2Error(f"{label} exact blob identity failed")


def minimum_reviewed_paths() -> set[str]:
    """Return the minimum I2/E0/static evidence set a future parent ticket must bind."""

    manifest = strict_json(E0_MANIFEST_PATH)
    if not isinstance(manifest, Mapping):
        raise P0P2Error("E0 manifest must be one object")
    snapshot = manifest.get("snapshot_copy")
    if not isinstance(snapshot, Mapping) or not isinstance(snapshot.get("files"), list):
        raise P0P2Error("E0 snapshot file identities are missing")
    snapshot_records = snapshot["files"]
    if not (
        len(snapshot_records) == snapshot.get("file_count")
        and all(
            isinstance(record, Mapping)
            and isinstance(record.get("path"), str)
            and record["path"]
            and not str(record["path"]).startswith("/")
            and ".." not in Path(str(record["path"])).parts
            for record in snapshot_records
        )
    ):
        raise P0P2Error("E0 snapshot file path identities are invalid")
    required = {
        relative(CONTRACT_PATH),
        relative(AUTHORIZATION_PATH),
        relative(E0_MANIFEST_PATH),
        relative(E0_AUDIT_PATH),
        relative(STATIC_ROOT / "p0p2_preflight_report.json"),
        relative(STATIC_ROOT / "p0p2_capability_identity.json"),
        relative(STATIC_ROOT / "p0p2_static_audit.json"),
        *(relative(HERE / name) for name in _IMPLEMENTATION_FILENAMES),
        *(relative(HERE / "tests" / name) for name in _TEST_FILENAMES),
        *(relative(source_config_path(benchmark)) for benchmark in ("B1", "B2", "B3")),
        *(
            relative(EFFECTIVE_CONFIG_ROOT / f"P0P2_{benchmark}.json")
            for benchmark in ("B1", "B2", "B3")
        ),
        *(
            relative(
                STATIC_ROOT
                / "formal_round_trip_reports"
                / f"P0P2_{benchmark}_FORMAL_ROUND_TRIP.json"
            )
            for benchmark in ("B1", "B2", "B3")
        ),
        *(
            relative(STATIC_ROOT / "probe_reports" / f"P0P2_{benchmark}_ZERO_STEP.json")
            for benchmark in ("B1", "B2", "B3")
        ),
        *(
            relative(E0_SNAPSHOT_ROOT / str(record["path"]))
            for record in snapshot_records
        ),
    }
    return required


def audit_authorized_repository(
    grant: Mapping[str, Any], *, repository: Path = REPOSITORY_ROOT
) -> dict[str, Any]:
    value = validate_authorized_grant(grant)
    repository = repository.resolve()
    child = _git_text(repository, "rev-parse", "HEAD")
    parent = _git_text(repository, "rev-parse", "HEAD^")
    if _git_text(repository, "rev-list", "--parents", "-n", "1", child).count(" ") != 1:
        raise P0P2Error("authorization child must have exactly one parent")
    if _git_text(repository, "rev-parse", "origin/main") != child:
        raise P0P2Error("authorization child must equal origin/main")
    if _git_bytes(repository, "status", "--porcelain=v1"):
        raise P0P2Error("authorization worktree must be clean")

    ticket_relative = relative(TICKET_PATH)
    ticket_bytes = _git_bytes(
        repository, "cat-file", "blob", f"{parent}:{ticket_relative}"
    )
    ticket_raw = strict_json_bytes(ticket_bytes, label=f"{parent}:{ticket_relative}")
    if not isinstance(ticket_raw, Mapping):
        raise P0P2Error("P0-P2 ticket must be one object")
    ticket = validate_ticket(ticket_raw)
    reviewed = ticket["reviewed_snapshot"]
    implementation = str(reviewed["commit"])
    if not (
        value["ticket_id"] == ticket["ticket_id"]
        and value["reviewed_snapshot_commit"] == implementation
        and _git_text(repository, "rev-parse", f"{implementation}^{{tree}}")
        == reviewed["tree"]
        and _git_text(repository, "rev-parse", f"{parent}^") == implementation
    ):
        raise P0P2Error("I2 -> P2 authorization ancestry or identity failed")

    review_paths = _nul_paths(repository, implementation, parent)
    allowed_paths = ticket["review_parent_policy"]["allowed_review_paths"]
    if sorted(review_paths) != sorted(allowed_paths):
        raise P0P2Error("I2..P2 review path set differs from ticket")
    protected = tuple(ticket["review_parent_policy"]["protected_prefixes"])
    if any(path.startswith(protected) for path in review_paths):
        raise P0P2Error("I2..P2 changed a protected prefix")
    implementation_root = relative(HERE) + "/"
    if any(
        path.startswith(implementation_root) and path != ticket_relative
        for path in review_paths
    ):
        raise P0P2Error("P2 changed an I2/E0 file other than the new parent ticket")
    if not {ticket_relative, str(ticket["ruling_path"])} <= set(review_paths):
        raise P0P2Error("P2 lacks exact ticket and ruling paths")

    child_paths = _nul_paths(repository, parent, child)
    if child_paths != [relative(AUTHORIZATION_PATH)]:
        raise P0P2Error("P2..C2 must modify only the authorization path")
    parent_tree = _tree_record(repository, parent, relative(AUTHORIZATION_PATH))
    child_tree = _tree_record(repository, child, relative(AUTHORIZATION_PATH))
    implementation_tree = _tree_record(
        repository, implementation, relative(AUTHORIZATION_PATH)
    )
    parent_bytes = _git_bytes(
        repository, "cat-file", "blob", f"{parent}:{relative(AUTHORIZATION_PATH)}"
    )
    child_bytes = _git_bytes(
        repository, "cat-file", "blob", f"{child}:{relative(AUTHORIZATION_PATH)}"
    )
    parent_value = strict_json_bytes(parent_bytes, label="P2 denied authorization")
    if not isinstance(parent_value, Mapping):
        raise P0P2Error("P2 denied authorization must be one object")
    validate_denied_authorization(parent_value)
    if implementation_tree != parent_tree:
        raise P0P2Error("P2 changed the reviewed default denial")
    child_value = strict_json_bytes(child_bytes, label="C2 authorized grant")
    if child_value != value:
        raise P0P2Error("worktree grant differs from C2 Git object")
    _validate_exact_blob(
        parent_bytes,
        parent_tree,
        ticket["denied_parent_blob"],
        label="denied parent",
    )
    _validate_exact_blob(
        child_bytes,
        child_tree,
        ticket["authorized_child_blob"],
        label="authorized child",
    )

    reviewed_files = reviewed["files"]
    observed_paths: set[str] = set()
    for index, record in enumerate(reviewed_files):
        if not isinstance(record, Mapping):
            raise P0P2Error("reviewed file binding is not an object")
        _validate_blob_record(
            repository, implementation, record, label=f"reviewed files[{index}]"
        )
        record_path = str(record["path"])
        if record_path in observed_paths:
            raise P0P2Error("ticket reviewed file bindings contain a duplicate path")
        observed_paths.add(record_path)
        implementation_record = _tree_record(repository, implementation, record_path)
        parent_record = _tree_record(repository, parent, record_path)
        if implementation_record != parent_record:
            raise P0P2Error("P2 changed a reviewed I2/E0/static evidence file")
    required_paths = minimum_reviewed_paths()
    if not required_paths <= observed_paths:
        raise P0P2Error("ticket does not bind all minimum P0-P2 contract files")
    return {
        "gate_pass": True,
        "authorization_child_commit": child,
        "review_parent_commit": parent,
        "reviewed_snapshot_commit": implementation,
        "ticket_id": ticket["ticket_id"],
        "ticket_sha256": hashlib.sha256(ticket_bytes).hexdigest(),
        "review_paths": review_paths,
        "authorization_paths": child_paths,
    }


def load_authorization(*, require_dynamic: bool = False) -> dict[str, Any]:
    raw = strict_json(AUTHORIZATION_PATH)
    if not isinstance(raw, Mapping):
        raise P0P2Error("P0-P2 authorization must be one object")
    if (
        raw.get("status") == DENIED_STATUS
        and raw.get("dynamic_samples_authorized") is False
    ):
        denied = validate_denied_authorization(raw)
        if require_dynamic:
            raise P0P2Error("HF3D_P0P2_DYNAMIC_EXECUTION_NOT_AUTHORIZED")
        return denied
    grant = validate_authorized_grant(raw)
    binding = audit_authorized_repository(grant)
    return {
        **grant,
        "authorization_gate_pass": True,
        "dynamic_execution_allowed": True,
        "observed_parent_ticket_binding": binding,
    }


__all__ = [
    "TICKET_STATUS",
    "audit_authorized_repository",
    "load_authorization",
    "minimum_reviewed_paths",
    "validate_authorized_grant",
    "validate_denied_authorization",
    "validate_ticket",
]
