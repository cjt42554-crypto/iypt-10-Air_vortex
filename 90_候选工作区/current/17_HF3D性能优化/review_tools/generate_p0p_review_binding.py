#!/usr/bin/env python3
"""Generate deterministic, non-authorizing P0-P review-binding material.

The output deliberately omits the commit and tree containing the material.
Those identities only exist after the review-only supplement is committed and
must be observed later by Pro from Git topology.  The older, already reviewed
runtime snapshot is recorded because it is not self-referential.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any

REVIEW_TOOLS_ROOT = Path(__file__).resolve().parent
HF3D_ROOT = REVIEW_TOOLS_ROOT.parent
if str(HF3D_ROOT) not in sys.path:
    sys.path.insert(0, str(HF3D_ROOT))

from p0_performance_contract import (
    AUTHORIZATION_ID,
    AUTHORIZATION_PATH,
    CONTRACT_ID,
    CONTRACT_PATH,
    EXPANDED_EXECUTION_SCHEDULE,
    FORMAL_SOURCE_SHA256,
    NOT_AUTHORIZED_STATUS,
    P0_D_EVIDENCE_HASHES,
    RAW_ROOT,
    REPOSITORY_ROOT,
    STATIC_ROOT,
    P0PerformanceError,
    formal_source_identity,
    load_contract,
    relative,
    strict_json_bytes,
)
from p0p_authorization import (
    _ALWAYS_PROTECTED_REVIEW_PREFIXES,
    _BLOB_RECORD_FIELDS,
    _BOUND_FILE_FIELDS,
    AUTHORIZATION_RELATIVE_PATH,
    AUTHORIZED_FIELDS,
    AUTHORIZED_STATUS,
    FIXED_SCHEDULE_ID,
    FORBIDDEN_SELF_FIELDS,
    REQUIRED_IMPLEMENTATION_RELATIVE_PATHS,
    TICKET_FIELDS,
    TICKET_PATH,
    TICKET_RELATIVE_PATH,
    TICKET_STATUS,
    _validate_production_static_audit,
    fixed_schedule_manifest,
    validate_denied_authorization,
)

MATERIAL_ID = "HF3D_P0P_REVIEW_BINDING_MATERIAL_V1"
MATERIAL_STATUS = "NON_AUTHORIZING_REVIEW_BINDING_MATERIAL_COMPLETE"
REVIEWED_RUNTIME_COMMIT = "8a0c2fa564d9d6abfd84e5620379af11e5f858d3"
REVIEWED_RUNTIME_TREE = "57dc939af0bc923ef3076d1d14ea889573041cf7"

STATIC_AUDIT_PATH = STATIC_ROOT / "p0p_static_audit.json"
CAPABILITY_MANIFEST_PATH = STATIC_ROOT / "p0p_capability_manifest.json"
MATERIAL_JSON_PATH = STATIC_ROOT / "p0p_review_binding_material.json"
MATERIAL_MARKDOWN_PATH = STATIC_ROOT / "p0p_review_binding_material.md"
CURRENT_HANDOFF_PATH = REPOSITORY_ROOT / "90_候选工作区" / "交接说明_current.md"
HISTORY_HANDOFF_PATH = REPOSITORY_ROOT / "90_候选工作区" / "交接说明_history.md"
FUTURE_RULING_DIRECTORY = "90_候选工作区/current"

EXPECTED_ZERO_COUNTERS = {
    "hf3d_time_steps_advanced": 0,
    "warmup_samples_started": 0,
    "scored_samples_started": 0,
    "profile_samples_started": 0,
    "performance_samples_started": 0,
}

MATERIAL_FORBIDDEN_REFERENCE_FIELDS = frozenset(
    {
        *FORBIDDEN_SELF_FIELDS,
        "current_commit",
        "current_tree",
        "material_commit",
        "material_tree",
        "supplement_commit",
        "supplement_tree",
        "future_parent_commit",
        "future_child_commit",
    }
)

TOP_LEVEL_FIELDS = frozenset(
    {
        "schema_version",
        "material_id",
        "status",
        "git_object_algorithm",
        "generation_invariants",
        "reviewed_runtime_reference",
        "bindings",
        "formal_source_identity",
        "fixed_schedule",
        "ticket_schema_preview",
        "review_parent_policy_preview",
        "authorization_state",
    }
)


class ReviewBindingError(P0PerformanceError):
    """Fail-closed error for review-binding generation or validation."""


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
        raise ReviewBindingError(f"Git plumbing failed for {arguments!r}: {detail}")
    return completed.stdout


def _git_text(repository: Path, *arguments: str) -> str:
    return _git(repository, *arguments).decode("utf-8", errors="strict").strip()


def _repository_path(path: object, label: str) -> str:
    if not isinstance(path, str) or not path or "\0" in path:
        raise ReviewBindingError(f"{label} path is invalid")
    parsed = PurePosixPath(path)
    if parsed.is_absolute() or parsed.as_posix() != path or ".." in parsed.parts:
        raise ReviewBindingError(f"{label} path is not normalized")
    return path


def _tree_path_exists(repository: Path, revision: str, path: str) -> bool:
    normalized = _repository_path(path, "Git tree")
    return bool(
        _git(
            repository,
            "ls-tree",
            "-z",
            revision,
            "--",
            f":(literal){normalized}",
        )
    )


def git_bound_file_record(
    repository: Path,
    revision: str,
    path: str,
    *,
    verify_worktree: bool = True,
    allowed_modes: frozenset[str] = frozenset({"100644", "100755"}),
) -> dict[str, Any]:
    """Read one exact regular blob through NUL-safe Git plumbing."""

    repository = repository.resolve()
    normalized = _repository_path(path, "bound file")
    output = _git(
        repository,
        "ls-tree",
        "-z",
        revision,
        "--",
        f":(literal){normalized}",
    )
    records = [record for record in output.split(b"\0") if record]
    if len(records) != 1:
        raise ReviewBindingError(f"bound path is missing or non-unique: {normalized}")
    try:
        header, raw_path = records[0].split(b"\t", 1)
        mode, object_type, oid = header.decode("ascii").split()
        observed_path = raw_path.decode("utf-8", errors="strict")
    except (UnicodeError, ValueError) as exc:
        raise ReviewBindingError("invalid NUL-safe ls-tree record") from exc
    if observed_path != normalized:
        raise ReviewBindingError("literal Git path lookup returned another path")
    if object_type != "blob" or mode not in allowed_modes:
        raise ReviewBindingError(
            f"bound path is not an allowed regular blob: {normalized} "
            f"mode={mode} type={object_type}"
        )
    payload = _git(repository, "cat-file", "blob", oid)
    record = {
        "path": normalized,
        "mode": mode,
        "git_blob": oid,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "byte_length": len(payload),
    }
    if verify_worktree:
        worktree_path = repository / normalized
        try:
            metadata = worktree_path.lstat()
        except FileNotFoundError as exc:
            raise ReviewBindingError(
                f"bound worktree file is missing: {normalized}"
            ) from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise ReviewBindingError(
                f"bound worktree path is not a regular file: {normalized}"
            )
        worktree_mode = "100755" if metadata.st_mode & 0o111 else "100644"
        if worktree_mode != mode or worktree_path.read_bytes() != payload:
            raise ReviewBindingError(
                f"bound worktree bytes or mode differ from Git: {normalized}"
            )
    return record


def _payload_for_record(repository: Path, record: Mapping[str, Any]) -> bytes:
    return _git(repository, "cat-file", "blob", str(record["git_blob"]))


def _json_for_record(
    repository: Path,
    record: Mapping[str, Any],
    *,
    label: str,
) -> dict[str, Any]:
    value = strict_json_bytes(_payload_for_record(repository, record), label=label)
    return dict(value)


def _record_for_path(
    repository: Path,
    revision: str,
    path: Path,
) -> dict[str, Any]:
    return git_bound_file_record(repository, revision, relative(path))


def _denied_ticket_blob_record(
    repository: Path,
    record: Mapping[str, Any],
    *,
    object_algorithm: str,
) -> dict[str, Any]:
    payload = _payload_for_record(repository, record)
    try:
        payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ReviewBindingError("denied authorization is not UTF-8") from exc
    if b"\r" in payload or not payload.endswith(b"\n"):
        raise ReviewBindingError("denied authorization is not LF-normalized")
    return {
        "git_oid": record["git_blob"],
        "git_object_algorithm": object_algorithm,
        "content_sha256": record["sha256"],
        "byte_length": record["byte_length"],
        "encoding": "UTF-8",
        "line_endings": "LF",
        "mode": record["mode"],
    }


def _walk_keys(value: object) -> list[str]:
    keys: list[str] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            keys.append(str(key))
            keys.extend(_walk_keys(child))
    elif isinstance(value, list):
        for child in value:
            keys.extend(_walk_keys(child))
    return keys


def _walk_strings(value: object) -> list[str]:
    strings: list[str] = []
    if isinstance(value, str):
        strings.append(value)
    elif isinstance(value, Mapping):
        for child in value.values():
            strings.extend(_walk_strings(child))
    elif isinstance(value, list):
        for child in value:
            strings.extend(_walk_strings(child))
    return strings


def _reject_self_or_future_references(
    value: Mapping[str, Any],
    *,
    current_commit: str,
    current_tree: str,
) -> None:
    forbidden_keys = MATERIAL_FORBIDDEN_REFERENCE_FIELDS & set(_walk_keys(value))
    if forbidden_keys:
        raise ReviewBindingError(
            "review material contains forbidden self/future fields: "
            + ", ".join(sorted(forbidden_keys))
        )
    strings = set(_walk_strings(value))
    if current_commit in strings or current_tree in strings:
        raise ReviewBindingError(
            "review material contains the commit/tree that currently contains it"
        )


def _assemble_material(repository: Path, revision: str) -> dict[str, Any]:
    repository = repository.resolve()
    current_commit = _git_text(repository, "rev-parse", f"{revision}^{{commit}}")
    current_tree = _git_text(repository, "rev-parse", f"{revision}^{{tree}}")
    reference_commit = _git_text(
        repository, "rev-parse", f"{REVIEWED_RUNTIME_COMMIT}^{{commit}}"
    )
    reference_tree = _git_text(
        repository, "rev-parse", f"{REVIEWED_RUNTIME_COMMIT}^{{tree}}"
    )
    if (
        reference_commit != REVIEWED_RUNTIME_COMMIT
        or reference_tree != REVIEWED_RUNTIME_TREE
    ):
        raise ReviewBindingError("reviewed runtime reference commit/tree drifted")

    object_algorithm = _git_text(repository, "rev-parse", "--show-object-format")
    implementation_paths = sorted(REQUIRED_IMPLEMENTATION_RELATIVE_PATHS)
    if len(implementation_paths) != len(REQUIRED_IMPLEMENTATION_RELATIVE_PATHS) or len(
        implementation_paths
    ) != len(set(implementation_paths)):
        raise ReviewBindingError("required implementation path universe is invalid")
    implementation_records = [
        git_bound_file_record(repository, revision, path)
        for path in implementation_paths
    ]
    reference_records = [
        git_bound_file_record(
            repository,
            REVIEWED_RUNTIME_COMMIT,
            path,
            verify_worktree=False,
        )
        for path in implementation_paths
    ]
    if implementation_records != reference_records:
        changed = [
            current["path"]
            for current, reference in zip(
                implementation_records, reference_records, strict=True
            )
            if current != reference
        ]
        raise ReviewBindingError(
            "runtime records differ from reviewed 8a0c2fa snapshot: "
            + ", ".join(changed)
        )

    static_audit_record = _record_for_path(repository, revision, STATIC_AUDIT_PATH)
    capability_record = _record_for_path(repository, revision, CAPABILITY_MANIFEST_PATH)
    contract_record = _record_for_path(repository, revision, CONTRACT_PATH)
    authorization_record = _record_for_path(repository, revision, AUTHORIZATION_PATH)
    p0_d_records = [
        _record_for_path(repository, revision, path)
        for path in sorted(P0_D_EVIDENCE_HASHES, key=relative)
    ]

    capability = _json_for_record(
        repository, capability_record, label="reviewed capability manifest"
    )
    static_audit = _json_for_record(
        repository, static_audit_record, label="reviewed P0-P static audit"
    )
    denied_authorization = _json_for_record(
        repository, authorization_record, label="default-denial authorization"
    )
    contract = _json_for_record(repository, contract_record, label="P0 contract")
    validate_denied_authorization(denied_authorization)
    _validate_production_static_audit(
        static_audit,
        capability_manifest=capability,
    )
    load_contract()
    if contract.get("contract_id") != CONTRACT_ID:
        raise ReviewBindingError("P0 contract identity is invalid")
    for record in p0_d_records:
        expected = P0_D_EVIDENCE_HASHES[repository / record["path"]]
        if record["sha256"] != expected:
            raise ReviewBindingError(f"P0-D identity drifted: {record['path']}")

    formal_identity = formal_source_identity()
    if formal_identity.get("sha256") != FORMAL_SOURCE_SHA256:
        raise ReviewBindingError("formal source identity drifted")
    if static_audit.get("counters") != EXPECTED_ZERO_COUNTERS:
        raise ReviewBindingError("static audit no longer proves zero execution")

    schedule = fixed_schedule_manifest()
    if (
        schedule.get("schedule_id") != FIXED_SCHEDULE_ID
        or len(schedule.get("ordered_items", [])) != 18
        or [item.get("sample_id") for item in schedule["ordered_items"]]
        != list(EXPANDED_EXECUTION_SCHEDULE)
    ):
        raise ReviewBindingError("fixed 18-item schedule identity is invalid")

    if TICKET_PATH.exists() or TICKET_PATH.is_symlink():
        raise ReviewBindingError("an actual Pro ticket is present")
    if _tree_path_exists(repository, revision, TICKET_RELATIVE_PATH):
        raise ReviewBindingError("the selected Git revision contains a Pro ticket")
    if RAW_ROOT.exists() and any(RAW_ROOT.iterdir()):
        raise ReviewBindingError("dynamic performance raw exists")

    immutable_paths = sorted(
        {
            AUTHORIZATION_RELATIVE_PATH,
            *implementation_paths,
            static_audit_record["path"],
            capability_record["path"],
            contract_record["path"],
            *(record["path"] for record in p0_d_records),
        }
    )
    denied_ticket_record = _denied_ticket_blob_record(
        repository,
        authorization_record,
        object_algorithm=object_algorithm,
    )
    material: dict[str, Any] = {
        "schema_version": 1,
        "material_id": MATERIAL_ID,
        "status": MATERIAL_STATUS,
        "git_object_algorithm": object_algorithm,
        "generation_invariants": {
            "derived_from_git_objects": True,
            "worktree_bytes_and_modes_must_match_git": True,
            "deterministic_without_timestamps": True,
            "regeneration_expected_byte_identical": True,
            "containing_commit_and_tree_intentionally_omitted": True,
            "future_parent_and_child_commit_ids_intentionally_omitted": True,
        },
        "reviewed_runtime_reference": {
            "commit_oid": REVIEWED_RUNTIME_COMMIT,
            "tree_oid": REVIEWED_RUNTIME_TREE,
            "required_implementation_file_count": len(implementation_records),
            "all_required_implementation_records_match": True,
        },
        "bindings": {
            "implementation_files": implementation_records,
            "static_audit": static_audit_record,
            "contract": contract_record,
            "p0_d_evidence": p0_d_records,
            "capability_manifest": capability_record,
            "denied_authorization": authorization_record,
        },
        "formal_source_identity": formal_identity,
        "fixed_schedule": schedule,
        "ticket_schema_preview": {
            "top_level_required_fields": sorted(TICKET_FIELDS),
            "ticket_static_values": {
                "schema_version": 1,
                "status": TICKET_STATUS,
                "authorization_path": AUTHORIZATION_RELATIVE_PATH,
            },
            "reviewed_snapshot_required_fields": [
                "commit_id",
                "tree_id",
                "static_audit",
                "implementation_files",
                "contract",
                "p0_d_evidence",
                "capability_manifest",
            ],
            "reviewed_snapshot_sources": {
                "commit_id": "OBSERVE_SUPPLEMENT_SNAPSHOT_I_AFTER_COMMIT",
                "tree_id": "OBSERVE_SUPPLEMENT_SNAPSHOT_I_AFTER_COMMIT",
                "static_audit": "bindings.static_audit",
                "implementation_files": "bindings.implementation_files",
                "contract": "bindings.contract",
                "p0_d_evidence": "bindings.p0_d_evidence",
                "capability_manifest": "bindings.capability_manifest",
            },
            "bound_file_record_fields": sorted(_BOUND_FILE_FIELDS),
            "blob_record_fields": sorted(_BLOB_RECORD_FIELDS),
            "denied_parent_blob_record": denied_ticket_record,
            "authorized_child_blob_record": {
                "present": False,
                "must_be_computed_by_pro_from_exact_future_grant_bytes": True,
                "required_fields": sorted(_BLOB_RECORD_FIELDS),
            },
            "toolchain_binding": capability,
            "authorized_grant_schema_preview": {
                "present": False,
                "required_fields": sorted(AUTHORIZED_FIELDS),
                "forbidden_self_fields": sorted(FORBIDDEN_SELF_FIELDS),
                "pro_filled_fields": [
                    "ticket_id",
                    "grant_nonce",
                    "reviewed_snapshot_commit",
                ],
                "exact_static_values": {
                    "schema_version": 2,
                    "authorization_id": AUTHORIZATION_ID,
                    "ticket_path": TICKET_RELATIVE_PATH,
                    "status": AUTHORIZED_STATUS,
                    "dynamic_samples_authorized": True,
                    "fixed_schedule_id": FIXED_SCHEDULE_ID,
                    "fixed_schedule_sha256": schedule["canonical_schedule_sha256"],
                    "maximum_schedule_claims": 1,
                    "preflight_consumes_claim": False,
                    "claim_before_first_sample": True,
                    "sample_replacement": "PROHIBITED",
                    "schedule_resume_after_claim": "PROHIBITED",
                    "P1": "PROHIBITED",
                },
            },
            "child_commit_policy": {
                "single_parent": True,
                "exactly_one_modified_path": AUTHORIZATION_RELATIVE_PATH,
                "old_mode": "100644",
                "new_mode": "100644",
                "rename_copy_symlink_submodule": "PROHIBITED",
            },
            "claim_policy": {
                "maximum_schedule_claims": 1,
                "preflight_consumes_claim": False,
                "claim_before_first_sample": True,
                "sample_replacement": "PROHIBITED",
                "schedule_resume_after_claim": "PROHIBITED",
                "retry_or_supplement": "PROHIBITED",
            },
        },
        "review_parent_policy_preview": {
            "actual_allowed_review_paths_must_be_exact_and_sorted": True,
            "required_exact_paths": [TICKET_RELATIVE_PATH],
            "optional_exact_paths_if_modified": sorted(
                [relative(CURRENT_HANDOFF_PATH), relative(HISTORY_HANDOFF_PATH)]
            ),
            "future_ruling_path_constraint": {
                "parent_directory": FUTURE_RULING_DIRECTORY,
                "suffix": ".md",
                "must_be_exactly_recorded_in_ticket": True,
                "must_be_regular_100644_blob": True,
            },
            "authorization_path_unchanged": True,
            "ticket_path": TICKET_RELATIVE_PATH,
            "protected_prefixes": sorted(_ALWAYS_PROTECTED_REVIEW_PREFIXES),
            "protected_exact_paths": ["AGENTS.md"],
            "immutable_bound_paths": immutable_paths,
            "allowed_delta_statuses": ["A", "M"],
            "allowed_new_mode": "100644",
            "rename_copy_symlink_submodule": "PROHIBITED",
        },
        "authorization_state": {
            "material_is_non_authorizing": True,
            "actual_pro_ticket_absent": True,
            "authorized_grant_absent": True,
            "authorization_status": NOT_AUTHORIZED_STATUS,
            "dynamic_samples_authorized": False,
            "p1_authorized": False,
            "performance_raw_absent_or_empty": True,
            "zero_step_counters": EXPECTED_ZERO_COUNTERS,
        },
    }
    _reject_self_or_future_references(
        material,
        current_commit=current_commit,
        current_tree=current_tree,
    )
    return material


def build_material(
    repository: Path = REPOSITORY_ROOT,
    revision: str = "HEAD",
) -> dict[str, Any]:
    """Build the canonical binding object from immutable Git records."""

    return _assemble_material(repository, revision)


def validate_material(
    material: Mapping[str, Any],
    repository: Path = REPOSITORY_ROOT,
    revision: str = "HEAD",
) -> dict[str, Any]:
    """Validate a material object against Git and the reviewed runtime."""

    repository = repository.resolve()
    current_commit = _git_text(repository, "rev-parse", f"{revision}^{{commit}}")
    current_tree = _git_text(repository, "rev-parse", f"{revision}^{{tree}}")
    _reject_self_or_future_references(
        material,
        current_commit=current_commit,
        current_tree=current_tree,
    )
    if set(material) != set(TOP_LEVEL_FIELDS):
        raise ReviewBindingError("review material top-level fields differ")

    bindings = material.get("bindings")
    if not isinstance(bindings, Mapping):
        raise ReviewBindingError("review material bindings are missing")
    records = bindings.get("implementation_files")
    if not isinstance(records, list):
        raise ReviewBindingError("implementation file records are not a list")
    paths = [
        record.get("path") if isinstance(record, Mapping) else None
        for record in records
    ]
    expected_paths = sorted(REQUIRED_IMPLEMENTATION_RELATIVE_PATHS)
    if len(paths) != len(set(paths)):
        raise ReviewBindingError("implementation file records contain duplicates")
    if set(paths) != set(expected_paths):
        missing = sorted(set(expected_paths) - set(paths))
        extra = sorted(set(paths) - set(expected_paths), key=str)
        raise ReviewBindingError(
            f"implementation file universe differs; missing={missing!r}, "
            f"extra={extra!r}"
        )
    if paths != expected_paths:
        raise ReviewBindingError("implementation file records are not ordered")

    expected = _assemble_material(repository, revision)
    expected_records = expected["bindings"]["implementation_files"]
    if records != expected_records:
        raise ReviewBindingError("implementation path/mode/blob/hash/length drifted")
    if material.get("fixed_schedule") != expected["fixed_schedule"]:
        raise ReviewBindingError("fixed schedule order or fields drifted")
    if material.get("formal_source_identity") != expected["formal_source_identity"]:
        raise ReviewBindingError("formal source identity drifted")
    if material.get("authorization_state") != expected["authorization_state"]:
        raise ReviewBindingError("non-authorizing zero-step state drifted")
    if dict(material) != expected:
        raise ReviewBindingError(
            "review material differs from deterministic Git-derived object"
        )
    return dict(material)


def material_json_bytes(material: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            material,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def material_markdown(material: Mapping[str, Any]) -> str:
    bindings = material["bindings"]
    implementation = bindings["implementation_files"]
    schedule = material["fixed_schedule"]
    state = material["authorization_state"]
    reference = material["reviewed_runtime_reference"]
    formal = material["formal_source_identity"]
    json_sha256 = hashlib.sha256(material_json_bytes(material)).hexdigest()

    lines = [
        "# HF3D P0-P review-binding material",
        "",
        "状态：`NON_AUTHORIZING_REVIEW_BINDING_MATERIAL_COMPLETE`。",
        "",
        "本材料只集中列出未来 Pro 父票据所需的机器绑定信息，不签发票据、不包含允许态 grant，也不授权任何动态样本。包含本材料的新提交及其 tree 身份被有意省略，必须在提交形成后由 Pro 从 Git 拓扑独立观测。",
        "",
        "## 确定性身份",
        "",
        f"- JSON 字节 SHA-256：`{json_sha256}`",
        f"- Git object format：`{material['git_object_algorithm']}`",
        f"- 已复核 runtime 参考提交：`{reference['commit_oid']}`",
        f"- 已复核 runtime 参考 tree：`{reference['tree_oid']}`",
        f"- REQUIRED_IMPLEMENTATION_FILES：`{reference['required_implementation_file_count']}` 项，全部记录与参考提交相同。",
        f"- formal source SHA-256：`{formal['sha256']}`，`{formal['file_count']}` 个文件，`{formal['total_bytes']}` 字节。",
        "",
        "## Runtime implementation 精确记录",
        "",
    ]
    for index, record in enumerate(implementation, start=1):
        lines.extend(
            [
                f"{index}. `{record['path']}`",
                "",
                f"   - mode：`{record['mode']}`",
                f"   - Git blob：`{record['git_blob']}`",
                f"   - SHA-256：`{record['sha256']}`",
                f"   - bytes：`{record['byte_length']}`",
                "",
            ]
        )
    lines.extend(
        [
            "## 其他票据绑定记录",
            "",
        ]
    )
    special_records = [
        ("static audit", bindings["static_audit"]),
        ("P0 contract", bindings["contract"]),
        ("capability manifest", bindings["capability_manifest"]),
        ("denied authorization", bindings["denied_authorization"]),
        *(("P0-D evidence", record) for record in bindings["p0_d_evidence"]),
    ]
    for index, (role, record) in enumerate(special_records, start=1):
        lines.extend(
            [
                f"{index}. {role}：`{record['path']}`",
                "",
                f"   - mode：`{record['mode']}`",
                f"   - Git blob：`{record['git_blob']}`",
                f"   - SHA-256：`{record['sha256']}`",
                f"   - bytes：`{record['byte_length']}`",
                "",
            ]
        )
    lines.extend(
        [
            "## 固定 18 项 schedule",
            "",
            f"Schedule ID：`{schedule['schedule_id']}`。",
            "",
            f"Canonical SHA-256：`{schedule['canonical_schedule_sha256']}`。",
            "",
        ]
    )
    for item in schedule["ordered_items"]:
        lines.extend(
            [
                f"{item['sequence_index'] + 1}. `{item['sample_id']}`",
                "",
                f"   - benchmark：`{item['benchmark_id']}`",
                f"   - mode：`{item['mode']}`",
                f"   - scored：`{str(item['scored']).lower()}`",
                f"   - source config SHA-256：`{item['source_config_sha256']}`",
                "",
            ]
        )
    lines.extend(
        [
            "## 授权与执行状态",
            "",
            f"- material non-authorizing：`{str(state['material_is_non_authorizing']).lower()}`",
            f"- actual Pro ticket absent：`{str(state['actual_pro_ticket_absent']).lower()}`",
            f"- authorized grant absent：`{str(state['authorized_grant_absent']).lower()}`",
            f"- dynamic samples authorized：`{str(state['dynamic_samples_authorized']).lower()}`",
            f"- authorization status：`{state['authorization_status']}`",
            f"- P1 authorized：`{str(state['p1_authorized']).lower()}`",
            "- HF3D time steps、warm-up、scored、profile 与全部性能样本启动数均为 `0`。",
            "",
            "## Future ticket policy preview",
            "",
            "未来父票据必须从 JSON 中的 `ticket_schema_preview`、`review_parent_policy_preview` 和精确绑定记录生成。Pro 仍需在 supplement 提交形成后独立观察 snapshot I 的 commit/tree，再生成 exact grant bytes 和 authorized child blob。父提交只允许精确列明的 review 路径；授权文件、全部 immutable bound paths、正式理论、正式程序和 D3 均不得变化。",
            "",
            "当前结论仍为 `P0-P dynamic samples = NOT_AUTHORIZED`。",
            "",
        ]
    )
    return "\n".join(lines)


def _atomic_replace(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
        directory_descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        if temporary.exists():
            temporary.unlink()


def write_material_outputs(
    material: Mapping[str, Any],
    *,
    json_path: Path = MATERIAL_JSON_PATH,
    markdown_path: Path = MATERIAL_MARKDOWN_PATH,
) -> None:
    _atomic_replace(json_path, material_json_bytes(material))
    _atomic_replace(markdown_path, material_markdown(material).encode("utf-8"))


def check_material_outputs(
    material: Mapping[str, Any],
    *,
    json_path: Path = MATERIAL_JSON_PATH,
    markdown_path: Path = MATERIAL_MARKDOWN_PATH,
) -> None:
    expected_json = material_json_bytes(material)
    expected_markdown = material_markdown(material).encode("utf-8")
    if not json_path.is_file() or json_path.is_symlink():
        raise ReviewBindingError("review-binding JSON is missing or not regular")
    if not markdown_path.is_file() or markdown_path.is_symlink():
        raise ReviewBindingError("review-binding Markdown is missing or not regular")
    if json_path.read_bytes() != expected_json:
        raise ReviewBindingError("review-binding JSON is not byte-identical")
    if markdown_path.read_bytes() != expected_markdown:
        raise ReviewBindingError("review-binding Markdown is not byte-identical")
    parsed = strict_json_bytes(json_path.read_bytes(), label="review-binding JSON")
    validate_material(parsed)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--write", action="store_true")
    action.add_argument("--check", action="store_true")
    parser.add_argument("--revision", default="HEAD")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        material = build_material(REPOSITORY_ROOT, args.revision)
        validate_material(material, REPOSITORY_ROOT, args.revision)
        if args.write:
            write_material_outputs(material)
        check_material_outputs(material)
    except Exception as exc:  # noqa: BLE001 - CLI must fail closed.
        print(
            f"P0-P review-binding material failed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 2
    print(
        json.dumps(
            {
                "status": MATERIAL_STATUS,
                "material_is_non_authorizing": True,
                "dynamic_samples_authorized": False,
                "required_implementation_files": len(
                    REQUIRED_IMPLEMENTATION_RELATIVE_PATHS
                ),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
