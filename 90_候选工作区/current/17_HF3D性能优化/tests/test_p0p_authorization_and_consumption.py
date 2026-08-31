from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import p0p_consumption as consumption
import run_p0_performance_campaign as campaign
from p0_performance_contract import (
    AUTHORIZATION_ID,
    AUTHORIZATION_PATH,
    EXPANDED_EXECUTION_SCHEDULE,
    P0PerformanceError,
    strict_json,
)
from p0p_authorization import (
    AUTHORIZATION_RELATIVE_PATH,
    AUTHORIZED_STATUS,
    FIXED_SCHEDULE_ID,
    TICKET_PATH,
    TICKET_RELATIVE_PATH,
    TICKET_STATUS,
    audit_authorized_repository,
    fixed_schedule_manifest,
    git_blob_oid,
    validate_authorized_grant,
    validate_denied_authorization,
)
from p0p_transaction import ExclusiveCampaignLock


def _run(
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
        raise AssertionError(
            completed.stderr.decode("utf-8", errors="replace")
            or completed.stdout.decode("utf-8", errors="replace")
        )
    return completed.stdout


def _text(repository: Path, *arguments: str) -> str:
    return _run(repository, *arguments).decode("utf-8").strip()


def _write_json(path: Path, value: Any) -> bytes:
    payload = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return payload


def _commit(repository: Path, message: str) -> str:
    _run(repository, "add", "--all")
    _run(repository, "commit", "-m", message)
    return _text(repository, "rev-parse", "HEAD")


def _bound_file(repository: Path, commit: str, path: str) -> dict[str, Any]:
    raw = _run(repository, "ls-tree", "-z", commit, "--", path).rstrip(b"\0")
    header, observed_path = raw.split(b"\t", 1)
    mode, object_type, oid = header.decode("ascii").split()
    if object_type != "blob" or observed_path.decode("utf-8") != path:
        raise AssertionError("synthetic bound file is not a blob")
    payload = _run(repository, "cat-file", "blob", f"{commit}:{path}")
    return {
        "path": path,
        "git_blob": oid,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "byte_length": len(payload),
        "mode": mode,
    }


def _blob_record(
    repository: Path, payload: bytes, *, oid: str | None = None
) -> dict[str, Any]:
    return {
        "git_oid": oid or git_blob_oid(repository, payload),
        "git_object_algorithm": _text(repository, "rev-parse", "--show-object-format"),
        "content_sha256": hashlib.sha256(payload).hexdigest(),
        "byte_length": len(payload),
        "encoding": "UTF-8",
        "line_endings": "LF",
        "mode": "100644",
    }


class SyntheticAuthorizationRepository:
    def __init__(
        self,
        root: Path,
        *,
        extra_review_path: bool = False,
        extra_child_path: bool = False,
        intermediate_parent: bool = False,
        wrong_denied_blob: bool = False,
        wrong_authorized_blob: bool = False,
        child_kind: str = "normal",
    ) -> None:
        self.repository = root / "repo"
        self.origin = root / "origin.git"
        self.repository.mkdir()
        _run(self.repository, "init", "-b", "main")
        _run(self.repository, "config", "user.email", "synthetic@example.invalid")
        _run(self.repository, "config", "user.name", "Synthetic P0P")

        denied = AUTHORIZATION_PATH.read_bytes()
        auth_path = self.repository / AUTHORIZATION_RELATIVE_PATH
        auth_path.parent.mkdir(parents=True, exist_ok=True)
        auth_path.write_bytes(denied)
        initial_files = {
            "合成/实现.py": b"VALUE = 1\n",
            "合成/static_audit.json": b'{"gate_pass":true}\n',
            "合成/contract.json": b'{"contract_id":"synthetic"}\n',
            "合成/p0d.json": b'{"status":"retained"}\n',
            "合成/capability.json": b'{"python":"synthetic"}\n',
        }
        for name, payload in initial_files.items():
            destination = self.repository / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(payload)
        self.implementation = _commit(self.repository, "I: reviewed implementation")
        if intermediate_parent:
            (self.repository / "合成/intermediate.txt").write_text(
                "unexpected parent\n", encoding="utf-8"
            )
            _commit(self.repository, "J: unexpected intermediate")

        schedule = fixed_schedule_manifest()
        self.grant = {
            "P1": "PROHIBITED",
            "authorization_id": AUTHORIZATION_ID,
            "claim_before_first_sample": True,
            "dynamic_samples_authorized": True,
            "fixed_schedule_id": FIXED_SCHEDULE_ID,
            "fixed_schedule_sha256": schedule["canonical_schedule_sha256"],
            "grant_nonce": "0123456789abcdef0123456789abcdef",
            "maximum_schedule_claims": 1,
            "preflight_consumes_claim": False,
            "reviewed_snapshot_commit": self.implementation,
            "sample_replacement": "PROHIBITED",
            "schedule_resume_after_claim": "PROHIBITED",
            "schema_version": 2,
            "status": AUTHORIZED_STATUS,
            "ticket_id": "SYNTHETIC_PARENT_TICKET_V1",
            "ticket_path": TICKET_RELATIVE_PATH,
        }
        authorized = (
            json.dumps(self.grant, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        ).encode("utf-8")
        ruling_path = "90_候选工作区/current/合成授权\n裁决.md"
        review_paths = [TICKET_RELATIVE_PATH, ruling_path]
        ticket = {
            "schema_version": 1,
            "ticket_id": self.grant["ticket_id"],
            "status": TICKET_STATUS,
            "authorization_path": AUTHORIZATION_RELATIVE_PATH,
            "ruling_path": ruling_path,
            "reviewed_snapshot": {
                "commit_id": self.implementation,
                "tree_id": _text(
                    self.repository, "rev-parse", f"{self.implementation}^{{tree}}"
                ),
                "static_audit": _bound_file(
                    self.repository, self.implementation, "合成/static_audit.json"
                ),
                "implementation_files": [
                    _bound_file(self.repository, self.implementation, "合成/实现.py")
                ],
                "contract": _bound_file(
                    self.repository, self.implementation, "合成/contract.json"
                ),
                "p0_d_evidence": [
                    _bound_file(self.repository, self.implementation, "合成/p0d.json")
                ],
                "capability_manifest": _bound_file(
                    self.repository, self.implementation, "合成/capability.json"
                ),
            },
            "review_parent_policy": {
                "allowed_review_paths": review_paths,
                "authorization_path_unchanged": True,
                "protected_prefixes": ["01_最终理论/", "02_模拟程序/"],
                "ticket_path": TICKET_RELATIVE_PATH,
                "ruling_path": ruling_path,
            },
            "denied_parent_blob": _blob_record(
                self.repository,
                denied,
                oid=("0" * 40 if wrong_denied_blob else None),
            ),
            "authorized_child_blob": _blob_record(
                self.repository,
                authorized,
                oid=("f" * 40 if wrong_authorized_blob else None),
            ),
            "fixed_schedule": schedule,
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
            "toolchain_binding": {"python": "synthetic"},
            "formal_source_tree_sha256": "a" * 64,
        }
        _write_json(self.repository / TICKET_RELATIVE_PATH, ticket)
        ruling = self.repository / ruling_path
        ruling.parent.mkdir(parents=True, exist_ok=True)
        ruling.write_text("# synthetic ruling\n", encoding="utf-8")
        if extra_review_path:
            (self.repository / "合成/unapproved.txt").write_text(
                "not ticketed\n", encoding="utf-8"
            )
        self.parent = _commit(self.repository, "P: parent ticket")

        if child_kind == "rename":
            renamed = auth_path.with_name("授权已重命名.json")
            auth_path.rename(renamed)
            renamed.write_bytes(authorized)
        elif child_kind == "symlink":
            auth_path.unlink()
            auth_path.symlink_to("合成目标")
        else:
            auth_path.write_bytes(authorized)
            if child_kind == "mode":
                auth_path.chmod(0o755)
            elif child_kind == "copy":
                copied = self.repository / "合成/copied-authorization.json"
                copied.write_bytes(authorized)
        if extra_child_path:
            (self.repository / "合成/extra-child.txt").write_text(
                "not authorization\n", encoding="utf-8"
            )
        self.child = _commit(self.repository, "C: exact authorization child")

        _run(root, "init", "--bare", str(self.origin))
        _run(self.repository, "remote", "add", "origin", str(self.origin))
        _run(self.repository, "push", "-u", "origin", "main")

    def audit(self) -> dict[str, Any]:
        return audit_authorized_repository(
            self.repository,
            self.grant,
            require_production_bindings=False,
        )


class AuthorizationTests(unittest.TestCase):
    def test_positive_parent_ticket_exact_blob_and_chinese_nul_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            synthetic = SyntheticAuthorizationRepository(Path(temporary))
            report = synthetic.audit()
            self.assertTrue(report["gate_pass"])
            self.assertEqual(
                report["reviewed_snapshot_commit"], synthetic.implementation
            )
            self.assertEqual(report["review_parent_commit"], synthetic.parent)
            self.assertEqual(report["authorization_child_commit"], synthetic.child)

    def test_grant_schema_forbids_every_self_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            synthetic = SyntheticAuthorizationRepository(Path(temporary))
            for field in (
                "authorization_child_commit",
                "self_commit_hash",
                "self_blob_hash",
                "review_parent_commit",
            ):
                malformed = {**synthetic.grant, field: "0" * 40}
                with self.subTest(field=field), self.assertRaises(P0PerformanceError):
                    validate_authorized_grant(malformed)

    def test_allowed_bytes_have_stable_pre_parent_blob(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            synthetic = SyntheticAuthorizationRepository(Path(temporary))
            payload = (
                json.dumps(
                    synthetic.grant,
                    ensure_ascii=False,
                    sort_keys=True,
                    indent=2,
                )
                + "\n"
            ).encode("utf-8")
            first = git_blob_oid(synthetic.repository, payload)
            second = git_blob_oid(synthetic.repository, payload)
            self.assertEqual(first, second)
            self.assertEqual(
                first,
                _text(
                    synthetic.repository,
                    "rev-parse",
                    f"{synthetic.child}:{AUTHORIZATION_RELATIVE_PATH}",
                ),
            )

    def test_wrong_review_or_child_deltas_fail_closed(self) -> None:
        cases: dict[str, dict[str, Any]] = {
            "unexpected_I_to_P_path": {"extra_review_path": True},
            "extra_P_to_C_path": {"extra_child_path": True},
            "rename": {"child_kind": "rename"},
            "copy": {"child_kind": "copy"},
            "mode": {"child_kind": "mode"},
            "symlink": {"child_kind": "symlink"},
            "P_parent_not_I": {"intermediate_parent": True},
        }
        for name, options in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                synthetic = SyntheticAuthorizationRepository(Path(temporary), **options)
                with self.assertRaises(P0PerformanceError):
                    synthetic.audit()

    def test_wrong_denied_or_authorized_blob_fails_closed(self) -> None:
        for option in ("wrong_denied_blob", "wrong_authorized_blob"):
            with (
                self.subTest(option=option),
                tempfile.TemporaryDirectory() as temporary,
            ):
                options: dict[str, Any] = {option: True}
                synthetic = SyntheticAuthorizationRepository(Path(temporary), **options)
                with self.assertRaises(P0PerformanceError):
                    synthetic.audit()

    def test_merge_child_and_local_origin_divergence_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            synthetic = SyntheticAuthorizationRepository(Path(temporary))
            _run(synthetic.repository, "checkout", "-b", "side")
            (synthetic.repository / "合成/side.txt").write_text(
                "side\n", encoding="utf-8"
            )
            _commit(synthetic.repository, "side")
            _run(synthetic.repository, "checkout", "main")
            _run(synthetic.repository, "merge", "--no-ff", "side", "-m", "merge")
            _run(synthetic.repository, "push", "origin", "main")
            with self.assertRaises(P0PerformanceError):
                synthetic.audit()

        with tempfile.TemporaryDirectory() as temporary:
            synthetic = SyntheticAuthorizationRepository(Path(temporary))
            _run(synthetic.repository, "commit", "--allow-empty", "-m", "local drift")
            with self.assertRaises(P0PerformanceError):
                synthetic.audit()

    def test_ticket_is_read_from_parent_object_not_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            synthetic = SyntheticAuthorizationRepository(Path(temporary))
            ticket_path = synthetic.repository / TICKET_RELATIVE_PATH
            ticket_path.write_text('{"malicious":"worktree"}\n', encoding="utf-8")
            _run(
                synthetic.repository,
                "update-index",
                "--assume-unchanged",
                TICKET_RELATIVE_PATH,
            )
            report = synthetic.audit()
            self.assertTrue(report["gate_pass"])
            self.assertEqual(
                report["ticket"]["ticket_id"], synthetic.grant["ticket_id"]
            )

    def test_default_denial_has_no_ticket_or_self_reference(self) -> None:
        denied = strict_json(AUTHORIZATION_PATH)
        validated = validate_denied_authorization(denied)
        self.assertIsNone(validated["ticket_id"])
        self.assertFalse(validated["dynamic_samples_authorized"])
        self.assertFalse(TICKET_PATH.exists())


@contextmanager
def _runtime_paths(root: Path) -> Iterator[None]:
    raw = root / "performance_raw"
    with patch.multiple(
        consumption,
        RAW_ROOT=raw,
        CLAIM_PATH=raw / "execution_claim.json",
        LEDGER_PATH=raw / "schedule_ledger.json",
        FAILURE_PATH=raw / "execution_failure.json",
        COMPLETE_PATH=raw / "execution_complete.json",
    ):
        yield


def _claim_inputs(lock_identity: dict[str, Any]) -> dict[str, Any]:
    ticket = {
        "ticket_id": "SYNTHETIC_PARENT_TICKET_V1",
        "authorized_child_blob": {"content_sha256": "b" * 64},
        "toolchain_binding": {"python": "synthetic"},
    }
    authorization = {
        "grant_nonce": "0123456789abcdef0123456789abcdef",
        "observed_parent_ticket_binding": {
            "gate_pass": True,
            "authorization_child_commit": "c" * 40,
            "review_parent_commit": "d" * 40,
            "reviewed_snapshot_commit": "e" * 40,
            "ticket": ticket,
            "ticket_blob": "a" * 40,
            "ticket_sha256": "a" * 64,
            "authorized_child_blob": "b" * 40,
        },
    }
    execution_binding = {
        "formal_source_tree": {"sha256": "f" * 64},
        "implementation_files": {"synthetic": {"sha256": "e" * 64}},
    }
    environment = {"instantaneous_start": {"snapshot": {"gpu": {"uuid": "GPU-X"}}}}
    return {
        "authorization": authorization,
        "execution_binding": execution_binding,
        "environment": environment,
        "lock_identity": lock_identity,
    }


class ConsumptionTests(unittest.TestCase):
    def test_invalid_zero_step_preflight_does_not_consume_or_create_raw(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            raw = Path(temporary) / "performance_raw"
            with (
                patch.object(campaign, "RAW_ROOT", raw),
                patch.object(campaign, "load_contract", return_value={"contract_id": "x"}),
                patch.object(campaign, "numerical_quality_gates", return_value={"x": True}),
                patch.object(campaign, "evidence_identity", return_value={"gate_pass": True}),
                patch.object(
                    campaign,
                    "load_authorization",
                    return_value={"dynamic_samples_authorized": False},
                ),
                patch.object(
                    campaign,
                    "assert_no_sample_output_root",
                    return_value={"empty": True},
                ),
                patch.object(campaign, "instantaneous_environment", return_value={}),
                patch.object(
                    campaign,
                    "evaluate_instantaneous_environment",
                    return_value={
                        "gate_pass": False,
                        "gates": {"synthetic_invalid": False},
                        "snapshot": {},
                    },
                ),
            ):
                report = campaign.zero_step_preflight()
            self.assertTrue(report["gate_pass"])
            self.assertFalse(report["environment_gate_pass"])
            self.assertEqual(report["p0p_dynamic_authorization"], "NOT_AUTHORIZED")
            self.assertFalse(raw.exists())

    def test_claim_is_o_excl_and_second_claim_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "artifacts").mkdir()
            lock_path = root / "campaign.lock"
            with (
                _runtime_paths(root / "artifacts"),
                ExclusiveCampaignLock(lock_path) as lock,
            ):
                first = consumption.create_claim_and_ledger(
                    **_claim_inputs(lock.identity)
                )
                self.assertTrue(first["claim_file"]["sha256"])
                with self.assertRaises(P0PerformanceError):
                    consumption.create_claim_and_ledger(**_claim_inputs(lock.identity))

    def test_strict_order_duplicate_skip_failure_and_no_resume(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "artifacts").mkdir()
            lock_path = root / "campaign.lock"
            with (
                _runtime_paths(root / "artifacts"),
                ExclusiveCampaignLock(lock_path) as lock,
            ):
                consumption.create_claim_and_ledger(**_claim_inputs(lock.identity))
                with self.assertRaises(P0PerformanceError):
                    consumption.mark_sample_running(1, "WARMUP_B2")
                token = consumption.mark_sample_running(0, "WARMUP_B1")
                with self.assertRaises(P0PerformanceError):
                    consumption.mark_sample_running(0, "WARMUP_B1")
                consumption.mark_schedule_failed(
                    sequence_index=0,
                    sample_id="WARMUP_B1",
                    failure={"reason": "synthetic crash"},
                )
                with self.assertRaises(P0PerformanceError):
                    consumption.mark_sample_running(1, "WARMUP_B2")
                self.assertTrue(token)

    def test_valid_worker_token_and_direct_or_pid_reuse_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "artifacts").mkdir()
            lock_path = root / "campaign.lock"
            with (
                _runtime_paths(root / "artifacts"),
                ExclusiveCampaignLock(lock_path) as lock,
            ):
                consumption.create_claim_and_ledger(**_claim_inputs(lock.identity))
                token = consumption.mark_sample_running(0, "WARMUP_B1")
                staging = Path(
                    tempfile.mkdtemp(
                        dir=consumption.RAW_ROOT,
                        prefix=".WARMUP_B1.staging.synthetic.",
                    )
                )
                valid = consumption.validate_worker_launch(
                    sample_id="WARMUP_B1",
                    sequence_index=0,
                    launch_token=token,
                    orchestrator_pid=os.getpid(),
                    lock_path=lock_path,
                    staging_path=staging,
                )
                self.assertTrue(valid["gate_pass"])
                with self.assertRaises(FileExistsError):
                    consumption.validate_worker_launch(
                        sample_id="WARMUP_B1",
                        sequence_index=0,
                        launch_token=token,
                        orchestrator_pid=os.getpid(),
                        lock_path=lock_path,
                        staging_path=staging,
                    )
                with self.assertRaises(P0PerformanceError):
                    consumption.validate_worker_launch(
                        sample_id="WARMUP_B1",
                        sequence_index=0,
                        launch_token="manual-direct-call",
                        orchestrator_pid=os.getpid(),
                        lock_path=lock_path,
                        staging_path=staging,
                    )
                with self.assertRaises(P0PerformanceError):
                    consumption.validate_worker_launch(
                        sample_id="WARMUP_B1",
                        sequence_index=0,
                        launch_token=token,
                        orchestrator_pid=os.getpid() + 1_000_000,
                        lock_path=lock_path,
                        staging_path=staging,
                    )

    def test_stale_lock_file_does_not_override_live_flock(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            lock_path = Path(temporary) / "campaign.lock"
            with (
                ExclusiveCampaignLock(lock_path),
                self.assertRaises(P0PerformanceError),
                ExclusiveCampaignLock(lock_path),
            ):
                self.fail("concurrent lock unexpectedly acquired")
            with ExclusiveCampaignLock(lock_path) as reacquired:
                self.assertEqual(reacquired.identity["pid"], os.getpid())

    def test_partial_claim_write_is_consumed_and_not_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "artifacts").mkdir()
            with (
                _runtime_paths(root / "artifacts"),
                patch.object(consumption.os, "write", side_effect=OSError("disk")),
                self.assertRaises(OSError),
            ):
                consumption.create_exclusive_json(
                    consumption.CLAIM_PATH, {"claim": "synthetic"}
                )
                self.assertTrue(consumption.CLAIM_PATH.exists())
                with self.assertRaises(FileExistsError):
                    consumption.create_exclusive_json(
                        consumption.CLAIM_PATH, {"claim": "replacement"}
                    )

    def test_ledger_initialization_failure_retains_claim_and_terminal_evidence(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "artifacts").mkdir()
            lock_path = root / "campaign.lock"
            original = consumption.create_exclusive_json

            def fail_ledger(
                path: Path, value: Mapping[str, Any]
            ) -> dict[str, Any]:
                if path == consumption.LEDGER_PATH:
                    raise OSError("synthetic ledger storage failure")
                return original(path, value)

            with (
                _runtime_paths(root / "artifacts"),
                ExclusiveCampaignLock(lock_path) as lock,
                patch.object(
                    consumption,
                    "create_exclusive_json",
                    side_effect=fail_ledger,
                ),
                self.assertRaises(OSError),
            ):
                consumption.create_claim_and_ledger(**_claim_inputs(lock.identity))
            runtime_root = root / "artifacts" / "performance_raw"
            self.assertTrue((runtime_root / "execution_claim.json").is_file())
            self.assertTrue((runtime_root / "execution_failure.json").is_file())
            with (
                _runtime_paths(root / "artifacts"),
                ExclusiveCampaignLock(lock_path) as lock,
                self.assertRaises(P0PerformanceError),
            ):
                consumption.create_claim_and_ledger(**_claim_inputs(lock.identity))

    def test_complete_and_failed_terminal_states_cannot_be_reused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "artifacts").mkdir()
            lock_path = root / "campaign.lock"
            with (
                _runtime_paths(root / "artifacts"),
                ExclusiveCampaignLock(lock_path) as lock,
            ):
                inputs = _claim_inputs(lock.identity)
                consumption.create_claim_and_ledger(**inputs)
                for index, sample_id in enumerate(EXPANDED_EXECUTION_SCHEDULE):
                    token = consumption.mark_sample_running(index, sample_id)
                    consumption.mark_sample_pass(
                        index,
                        sample_id,
                        launch_token=token,
                        manifest_sha256="a" * 64,
                    )
                child_commit = str(
                    inputs["authorization"]["observed_parent_ticket_binding"][
                        "authorization_child_commit"
                    ]
                )
                completion = {
                    "schema_version": 1,
                    "status": "HF3D_P0P_FROZEN_SCHEDULE_EXECUTED_PENDING_ANALYSIS",
                    "gate_pass": True,
                    "authorization_child_commit": child_commit,
                    "completed_samples": list(EXPANDED_EXECUTION_SCHEDULE),
                    "completed_monotonic_ns": time.monotonic_ns(),
                }
                consumption.publish_execution_complete(completion)
                with self.assertRaises(P0PerformanceError):
                    consumption.mark_sample_running(0, EXPANDED_EXECUTION_SCHEDULE[0])
                with self.assertRaises(P0PerformanceError):
                    consumption.publish_execution_failure(
                        {
                            "schema_version": 1,
                            "status": "INVALID_AFTER_COMPLETE",
                            "gate_pass": False,
                        }
                    )
                with self.assertRaises(FileExistsError):
                    consumption.publish_execution_complete(completion)
                with self.assertRaises(P0PerformanceError):
                    consumption.create_claim_and_ledger(**inputs)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "artifacts").mkdir()
            lock_path = root / "campaign.lock"
            with (
                _runtime_paths(root / "artifacts"),
                ExclusiveCampaignLock(lock_path) as lock,
            ):
                inputs = _claim_inputs(lock.identity)
                consumption.create_claim_and_ledger(**inputs)
                token = consumption.mark_sample_running(
                    0, EXPANDED_EXECUTION_SCHEDULE[0]
                )
                self.assertTrue(token)
                failure = {
                    "schema_version": 1,
                    "status": "HF3D_P0P_SYNTHETIC_FAILED_STOPPED",
                    "gate_pass": False,
                }
                consumption.mark_schedule_failed(
                    sequence_index=0,
                    sample_id=EXPANDED_EXECUTION_SCHEDULE[0],
                    failure=failure,
                )
                consumption.publish_execution_failure(failure)
                with self.assertRaises(P0PerformanceError):
                    consumption.mark_sample_running(1, EXPANDED_EXECUTION_SCHEDULE[1])
                with self.assertRaises(P0PerformanceError):
                    consumption.publish_execution_complete(
                        {
                            "schema_version": 1,
                            "status": "HF3D_P0P_FROZEN_SCHEDULE_EXECUTED_PENDING_ANALYSIS",
                            "gate_pass": True,
                            "authorization_child_commit": "c" * 40,
                            "completed_samples": list(EXPANDED_EXECUTION_SCHEDULE),
                            "completed_monotonic_ns": time.monotonic_ns(),
                        }
                    )
                with self.assertRaises(FileExistsError):
                    consumption.publish_execution_failure(failure)
                with self.assertRaises(P0PerformanceError):
                    consumption.create_claim_and_ledger(**inputs)


if __name__ == "__main__":
    unittest.main()
