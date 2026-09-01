from __future__ import annotations

import copy
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
from typing import Any, Self
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import p0p2v2_consumption as consumption
import run_p0p2v2_campaign as campaign
from p0p2v2_authorization import (
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
from p0p2v2_contract import (
    AUTHORIZATION_ID,
    AUTHORIZATION_PATH,
    CONTRACT_ID,
    EXPANDED_EXECUTION_SCHEDULE,
    GRANT_NONCE_PREFIX,
    P0PerformanceError,
    strict_json,
)
from p0p2v2_transaction import ExclusiveCampaignLock


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
        parent_merge: bool = False,
        reviewed_binding_kind: str = "normal",
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
            "90_候选工作区/交接说明_current.md": b"# initial current\n",
            "90_候选工作区/交接说明_history.md": b"# initial history\n",
        }
        for name, payload in initial_files.items():
            destination = self.repository / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(payload)
        self.implementation = _commit(self.repository, "I: reviewed implementation")
        if parent_merge:
            _run(self.repository, "checkout", "-b", "synthetic-parent-side")
            _run(
                self.repository,
                "commit",
                "--allow-empty",
                "-m",
                "side parent",
            )
            _run(self.repository, "checkout", "main")
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
            "contract_id": CONTRACT_ID,
            "grant_nonce": GRANT_NONCE_PREFIX
            + "0123456789abcdef0123456789abcdef",
            "maximum_schedule_claims": 1,
            "preflight_consumes_claim": False,
            "reviewed_snapshot_commit": self.implementation,
            "sample_replacement": "PROHIBITED",
            "schedule_resume_after_claim": "PROHIBITED",
            "schedule_retry_or_supplement": "PROHIBITED",
            "schema_version": 3,
            "status": AUTHORIZED_STATUS,
            "ticket_id": "SYNTHETIC_PARENT_TICKET_V1",
            "ticket_path": TICKET_RELATIVE_PATH,
        }
        authorized = (
            json.dumps(self.grant, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        ).encode("utf-8")
        ruling_path = "90_候选工作区/current/合成授权\n裁决.md"
        review_paths = [
            TICKET_RELATIVE_PATH,
            ruling_path,
            "90_候选工作区/交接说明_current.md",
            "90_候选工作区/交接说明_history.md",
        ]
        implementation_record = _bound_file(
            self.repository, self.implementation, "合成/实现.py"
        )
        if reviewed_binding_kind == "missing":
            implementation_record = {
                **implementation_record,
                "path": "合成/不存在.py",
            }
        elif reviewed_binding_kind == "drifted":
            implementation_record = {
                **implementation_record,
                "sha256": "0" * 64,
            }
        elif reviewed_binding_kind != "normal":
            raise ValueError("unknown reviewed binding kind")
        ticket = {
            "schema_version": 2,
            "ticket_id": self.grant["ticket_id"],
            "contract_id": CONTRACT_ID,
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
                "implementation_files": [implementation_record],
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
                "current_path": "90_候选工作区/交接说明_current.md",
                "history_path": "90_候选工作区/交接说明_history.md",
                "metadata_mode": "100644",
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
                "schedule_retry_or_supplement": "PROHIBITED",
            },
            "toolchain_binding": {"python": "synthetic"},
            "formal_source_tree_sha256": "a" * 64,
        }
        _write_json(self.repository / TICKET_RELATIVE_PATH, ticket)
        ruling = self.repository / ruling_path
        ruling.parent.mkdir(parents=True, exist_ok=True)
        ruling.write_text("# synthetic ruling\n", encoding="utf-8")
        (self.repository / "90_候选工作区/交接说明_current.md").write_text(
            "# reviewed current\n", encoding="utf-8"
        )
        (self.repository / "90_候选工作区/交接说明_history.md").write_text(
            "# reviewed history\n", encoding="utf-8"
        )
        if extra_review_path:
            (self.repository / "合成/unapproved.txt").write_text(
                "not ticketed\n", encoding="utf-8"
            )
        self.parent = _commit(self.repository, "P: parent ticket")
        if parent_merge:
            _run(
                self.repository,
                "merge",
                "--no-ff",
                "synthetic-parent-side",
                "-m",
                "merge parent",
            )
            self.parent = _text(self.repository, "rev-parse", "HEAD")

        if child_kind == "rename":
            renamed = auth_path.with_name("授权已重命名.json")
            auth_path.rename(renamed)
            renamed.write_bytes(authorized)
        elif child_kind == "symlink":
            auth_path.unlink()
            auth_path.symlink_to("合成目标")
        elif child_kind == "submodule":
            auth_path.unlink()
            _run(
                self.repository,
                "update-index",
                "--add",
                "--cacheinfo",
                f"160000,{self.implementation},{AUTHORIZATION_RELATIVE_PATH}",
            )
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
        if child_kind == "submodule":
            _run(self.repository, "commit", "-m", "C: submodule authorization")
            self.child = _text(self.repository, "rev-parse", "HEAD")
        else:
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
        "grant_nonce": GRANT_NONCE_PREFIX + "0123456789abcdef0123456789abcdef",
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
    environment = {"instantaneous_end": {"snapshot": {"gpu": {"uuid": "GPU-X"}}}}
    return {
        "authorization": authorization,
        "execution_binding": execution_binding,
        "environment": environment,
        "machine_identity": ticket["toolchain_binding"],
        "lock_identity": lock_identity,
    }


@contextmanager
def _synthetic_campaign(
    root: Path,
    launches: list[dict[str, Any]],
) -> Iterator[Path]:
    artifacts = root / "artifacts"
    artifacts.mkdir()
    raw = artifacts / "performance_raw"
    lock_path = root / "campaign.lock"
    inputs = _claim_inputs({"synthetic": True})
    preflight = {
        "gate_pass": True,
        "post_probe_environment": inputs["environment"],
        "observed_capability_identity": inputs["machine_identity"],
    }

    def execute_sample(
        sample_id: str,
        *,
        sequence_index: int,
        launch_token: str,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        manifest = {
            "sample_id": sample_id,
            "sequence_index": sequence_index,
            "launch_token": launch_token,
        }
        launches.append(manifest)
        _write_json(raw / sample_id / "sample_manifest.json", manifest)
        return manifest

    with (
        _runtime_paths(artifacts),
        patch.object(campaign, "RAW_ROOT", raw),
        patch.object(campaign, "CAMPAIGN_LOCK_PATH", lock_path),
        patch.object(
            campaign,
            "load_authorization",
            return_value=inputs["authorization"],
        ),
        patch.object(campaign, "load_contract", return_value={}),
        patch.object(campaign, "evidence_identity", return_value={}),
        patch.object(campaign, "assert_no_sample_output_root", return_value={}),
        patch.object(campaign, "zero_step_preflight", return_value=preflight),
        patch.object(
            campaign,
            "execution_binding",
            return_value=inputs["execution_binding"],
        ),
        patch.object(
            campaign,
            "full_environment_gate",
            return_value={"gate_pass": True},
        ),
        patch.object(campaign, "execute_one_sample", side_effect=execute_sample),
    ):
        yield raw


class ConsumptionTests(unittest.TestCase):
    def test_preclaim_failure_is_retained_while_authorized_tree_stays_clean(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary) / "repository"
            repository.mkdir()
            _run(repository, "init", "-b", "main")
            _run(repository, "config", "user.name", "Synthetic")
            _run(repository, "config", "user.email", "synthetic@example.invalid")
            (repository / ".gitignore").write_text(
                "artifacts/p0p2v2/static/preclaim_failures/\n",
                encoding="utf-8",
            )
            (repository / "tracked.txt").write_text("clean\n", encoding="utf-8")
            _commit(repository, "synthetic authorized child")
            static_root = repository / "artifacts" / "p0p2v2" / "static"
            static_root.mkdir(parents=True)
            failure_root = static_root / "preclaim_failures"
            record = {
                "schema_version": 2,
                "status": "HF3D_P0P2_V2_PREFLIGHT_FAILED_BEFORE_CLAIM",
                "gate_pass": False,
            }
            with (
                patch.object(campaign, "REPOSITORY_ROOT", repository),
                patch.object(campaign, "PRECLAIM_FAILURE_ROOT", failure_root),
            ):
                first = campaign._publish_preclaim_failure(record)
                second = campaign._publish_preclaim_failure(
                    {**record, "status": "SECOND_NONCONSUMING_PREFLIGHT_FAILURE"}
                )
            self.assertNotEqual(first, second)
            self.assertEqual(strict_json(first), record)
            self.assertEqual(
                strict_json(second)["status"],
                "SECOND_NONCONSUMING_PREFLIGHT_FAILURE",
            )
            self.assertEqual(
                _run(
                    repository,
                    "status",
                    "--porcelain=v2",
                    "-z",
                    "--untracked-files=all",
                ),
                b"",
            )
            self.assertEqual(
                _text(repository, "rev-parse", "HEAD"),
                _text(repository, "rev-parse", "main"),
            )
            self.assertFalse(
                (repository / "artifacts/p0p2v2/performance_raw").exists()
            )
            self.assertFalse(
                (repository / "artifacts/p0p2v2/.p0p2v2_campaign.lock").exists()
            )

            class ReachedClaim(RuntimeError):
                pass

            class FakeLock:
                def __init__(self) -> None:
                    self.identity = {"synthetic": True}

                def __enter__(self) -> Self:
                    return self

                def __exit__(self, *_args: object) -> None:
                    return None

            valid_preflight = {
                "gate_pass": True,
                "post_probe_environment": {"gate_pass": True},
                "observed_capability_identity": {"machine": "exact"},
            }
            with (
                patch.object(campaign, "REPOSITORY_ROOT", repository),
                patch.object(campaign, "PRECLAIM_FAILURE_ROOT", failure_root),
                patch.object(
                    campaign,
                    "RAW_ROOT",
                    repository / "artifacts/p0p2v2/performance_raw",
                ),
                patch.object(campaign, "load_authorization", return_value={}),
                patch.object(campaign, "load_contract", return_value={}),
                patch.object(campaign, "evidence_identity", return_value={}),
                patch.object(campaign, "assert_no_sample_output_root", return_value={}),
                patch.object(
                    campaign, "zero_step_preflight", return_value=valid_preflight
                ),
                patch.object(campaign, "execution_binding", return_value={}),
                patch.object(campaign, "ExclusiveCampaignLock", return_value=FakeLock()),
                patch.object(
                    campaign,
                    "create_claim_and_ledger",
                    side_effect=ReachedClaim("same clean C2 reached claim boundary"),
                ) as create_claim,
                self.assertRaises(ReachedClaim),
            ):
                campaign.execute_campaign()
            create_claim.assert_called_once()
            self.assertEqual(
                _run(
                    repository,
                    "status",
                    "--porcelain=v2",
                    "-z",
                    "--untracked-files=all",
                ),
                b"",
            )

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
            self.assertFalse(report["gate_pass"])
            self.assertFalse(report["static_qualification"]["gate_pass"])
            self.assertEqual(
                report["p0p2v2_dynamic_authorization"], "NOT_AUTHORIZED"
            )
            self.assertFalse(raw.exists())

    def test_ticket_machine_drift_precedes_every_runtime_transaction(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = root / "performance_raw"
            lock = root / "campaign.lock"
            expected = {"host_policy": {"governors": {"cpu0": "powersave"}}}
            authorization = {
                "status": AUTHORIZED_STATUS,
                "dynamic_samples_authorized": True,
                "observed_parent_ticket_binding": {
                    "ticket": {"toolchain_binding": expected}
                },
            }
            sequence = iter((101, 102, 103))

            def probe(_benchmark: str) -> dict[str, Any]:
                pid = next(sequence)
                return {
                    "gate_pass": True,
                    "report": {
                        "process_identity": {
                            "pid": pid,
                            "process_start_ticks": pid + 1000,
                        }
                    },
                }

            with (
                patch.object(campaign, "RAW_ROOT", raw),
                patch.object(campaign, "CAMPAIGN_LOCK_PATH", lock),
                patch.object(campaign, "load_contract", return_value={"contract_id": "x"}),
                patch.object(campaign, "numerical_quality_gates", return_value={"x": True}),
                patch.object(campaign, "evidence_identity", return_value={"gate_pass": True}),
                patch.object(campaign, "load_authorization", return_value=authorization),
                patch.object(
                    campaign,
                    "assert_no_sample_output_root",
                    return_value={"empty": True},
                ),
                patch.object(
                    campaign,
                    "evaluate_instantaneous_environment",
                    return_value={"gate_pass": True, "snapshot": {}},
                ),
            ):
                report = campaign.zero_step_preflight(
                    initial_snapshot_runner=dict,
                    probe_runner=probe,
                    environment_runner=lambda **_kwargs: {
                        "gate_pass": True,
                        "instantaneous_end": {"snapshot": {}},
                    },
                    capability_runner=lambda _probes, _environment: {
                        "host_policy": {"governors": {"cpu0": "performance"}}
                    },
                )
            self.assertFalse(report["gate_pass"])
            self.assertFalse(
                report["gates"][
                    "ticket_bound_machine_identity_exact_before_runtime_transactions"
                ]
            )
            self.assertFalse(raw.exists())
            self.assertFalse(lock.exists())
            with (
                patch.object(campaign, "load_authorization", return_value=authorization),
                patch.object(campaign, "load_contract", return_value={"contract_id": "x"}),
                patch.object(campaign, "evidence_identity", return_value={"gate_pass": True}),
                patch.object(
                    campaign,
                    "assert_no_sample_output_root",
                    return_value={"empty": True},
                ),
                patch.object(campaign, "zero_step_preflight", return_value=report),
                patch.object(campaign, "_publish_preclaim_failure") as publish_failure,
                patch.object(campaign, "execution_binding") as bind_execution,
                patch.object(campaign, "ExclusiveCampaignLock") as acquire_lock,
                self.assertRaises(P0PerformanceError),
            ):
                campaign.execute_campaign()
            publish_failure.assert_called_once()
            bind_execution.assert_not_called()
            acquire_lock.assert_not_called()

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

    def test_each_sample_full_gate_precedes_running_and_staging(self) -> None:
        class FakeLock:
            def __init__(self) -> None:
                self.identity = {"synthetic": True}

            def __enter__(self) -> Self:
                return self

            def __exit__(self, *_args: object) -> None:
                return None

        with tempfile.TemporaryDirectory() as temporary:
            raw = Path(temporary) / "performance_raw"
            preflight = {
                "gate_pass": True,
                "post_probe_environment": {"gate_pass": True},
                "observed_capability_identity": {"machine": "exact"},
            }

            def create_claim(**_kwargs: Any) -> dict[str, Any]:
                raw.mkdir()
                return {"claim": "synthetic"}

            def publish_failure(_record: Mapping[str, Any]) -> dict[str, Any]:
                (raw / "execution_failure.json").write_text("{}\n", encoding="utf-8")
                return {"path": str(raw / "execution_failure.json")}

            with (
                patch.object(campaign, "RAW_ROOT", raw),
                patch.object(
                    campaign,
                    "load_authorization",
                    return_value={
                        "observed_parent_ticket_binding": {
                            "authorization_child_commit": "c" * 40
                        }
                    },
                ),
                patch.object(campaign, "load_contract", return_value={}),
                patch.object(campaign, "evidence_identity", return_value={}),
                patch.object(campaign, "assert_no_sample_output_root", return_value={}),
                patch.object(campaign, "zero_step_preflight", return_value=preflight),
                patch.object(campaign, "execution_binding", return_value={}),
                patch.object(campaign, "ExclusiveCampaignLock", return_value=FakeLock()),
                patch.object(
                    campaign,
                    "create_claim_and_ledger",
                    side_effect=create_claim,
                ),
                patch.object(
                    campaign,
                    "full_environment_gate",
                    return_value={"gate_pass": False, "stage": "synthetic"},
                ),
                patch.object(campaign, "mark_schedule_failed") as mark_failed,
                patch.object(
                    campaign,
                    "publish_execution_failure",
                    side_effect=publish_failure,
                ),
                patch.object(campaign, "mark_sample_running") as mark_running,
                patch.object(campaign, "execute_one_sample") as execute_sample,
                self.assertRaises(P0PerformanceError),
            ):
                campaign.execute_campaign()
            mark_failed.assert_called_once()
            mark_running.assert_not_called()
            execute_sample.assert_not_called()
            self.assertFalse(any(path.name.startswith(".") for path in raw.iterdir()))

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
                    consumption.mark_sample_running(
                        1, EXPANDED_EXECUTION_SCHEDULE[1]
                    )
                token = consumption.mark_sample_running(
                    0, EXPANDED_EXECUTION_SCHEDULE[0]
                )
                with self.assertRaises(P0PerformanceError):
                    consumption.mark_sample_running(
                        0, EXPANDED_EXECUTION_SCHEDULE[0]
                    )
                consumption.mark_schedule_failed(
                    sequence_index=0,
                    sample_id=EXPANDED_EXECUTION_SCHEDULE[0],
                    failure={"reason": "synthetic crash"},
                )
                with self.assertRaises(P0PerformanceError):
                    consumption.mark_sample_running(
                        1, EXPANDED_EXECUTION_SCHEDULE[1]
                    )
                self.assertTrue(token)

    def test_running_transition_recovers_one_transient_strict_reload(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "artifacts").mkdir()
            lock_path = root / "campaign.lock"
            original_strict_json = consumption.strict_json
            injected = False

            def transient_candidate_reload(path: Path) -> dict[str, Any]:
                nonlocal injected
                observed = original_strict_json(path)
                entries = observed.get("entries")
                if (
                    path == consumption.LEDGER_PATH
                    and isinstance(entries, list)
                    and entries[0].get("state") == "RUNNING"
                    and not injected
                ):
                    injected = True
                    raise OSError("synthetic first strict reload fault")
                return observed

            with (
                _runtime_paths(root / "artifacts"),
                ExclusiveCampaignLock(lock_path) as lock,
            ):
                consumption.create_claim_and_ledger(**_claim_inputs(lock.identity))
                with patch.object(
                    consumption,
                    "strict_json",
                    side_effect=transient_candidate_reload,
                ):
                    token = consumption.mark_sample_running(
                        0, EXPANDED_EXECUTION_SCHEDULE[0]
                    )
                ledger = consumption.load_ledger()
                self.assertTrue(injected)
                self.assertEqual(ledger["entries"][0]["start_count"], 1)
                self.assertEqual(
                    ledger["entries"][0]["launch_token_sha256"],
                    hashlib.sha256(token.encode("utf-8")).hexdigest(),
                )
                failure = {
                    "schema_version": 2,
                    "status": "HF3D_P0P2_V2_TRANSIENT_RELOAD_TEST_FAILED_STOPPED",
                    "gate_pass": False,
                }
                consumption.mark_schedule_failed(
                    sequence_index=0,
                    sample_id=EXPANDED_EXECUTION_SCHEDULE[0],
                    failure=failure,
                )
                consumption.publish_execution_failure(failure)
                self.assertEqual(
                    consumption.validate_terminal_execution_state()["terminal_kind"],
                    "FAILED_STOPPED",
                )

    def test_full_orchestrator_ledger_transition_fault_matrix(self) -> None:
        transition_modes = ("RUNNING", "ORDINARY_PASS", "FINAL_PENDING")
        fault_modes = ("PRECOMMIT_ONCE", "POSTCOMMIT", "PRECOMMIT_PERSISTENT")

        def matches_target(ledger: Mapping[str, Any], mode: str) -> bool:
            entries = ledger.get("entries")
            if not isinstance(entries, list):
                return False
            if mode == "RUNNING":
                return entries[0].get("state") == "RUNNING" and all(
                    entry.get("state") == "NOT_STARTED" for entry in entries[1:]
                )
            if mode == "ORDINARY_PASS":
                return (
                    ledger.get("status") == "IN_PROGRESS"
                    and entries[0].get("state") == "PASS"
                    and entries[1].get("state") == "NOT_STARTED"
                )
            return ledger.get("status") == "ALL_SAMPLES_PASS_PENDING_TERMINAL"

        for transition_mode in transition_modes:
            for fault_mode in fault_modes:
                with (
                    self.subTest(
                        transition=transition_mode,
                        fault=fault_mode,
                    ),
                    tempfile.TemporaryDirectory() as temporary,
                ):
                    root = Path(temporary)
                    launches: list[dict[str, Any]] = []
                    attempted_candidates: list[dict[str, Any]] = []
                    with _synthetic_campaign(root, launches):
                        original_publish = consumption._publish_ledger

                        def inject_transition_fault(
                            ledger: Mapping[str, Any],
                            target_mode: str = transition_mode,
                            fault_kind: str = fault_mode,
                            candidates: list[dict[str, Any]] = attempted_candidates,
                            publish: Any = original_publish,
                        ) -> None:
                            if matches_target(ledger, target_mode):
                                candidates.append(
                                    copy.deepcopy(dict(ledger))
                                )
                                attempt = len(candidates)
                                if fault_kind == "POSTCOMMIT" and attempt == 1:
                                    publish(ledger)
                                    raise OSError(
                                        "synthetic post-commit ledger fault"
                                    )
                                if fault_kind == "PRECOMMIT_ONCE" and attempt == 1:
                                    raise OSError(
                                        "synthetic one-shot pre-commit ledger fault"
                                    )
                                if (
                                    fault_kind == "PRECOMMIT_PERSISTENT"
                                    and attempt <= 2
                                ):
                                    raise OSError(
                                        "synthetic persistent pre-commit ledger fault"
                                    )
                            publish(ledger)

                        with patch.object(
                            consumption,
                            "_publish_ledger",
                            side_effect=inject_transition_fault,
                        ):
                            if fault_mode == "PRECOMMIT_PERSISTENT":
                                with self.assertRaises(
                                    consumption.LedgerTransitionNotCommitted
                                ):
                                    campaign.execute_campaign()
                            else:
                                result = campaign.execute_campaign()
                                self.assertTrue(result["gate_pass"])

                        terminal = consumption.validate_terminal_execution_state()
                        expected_terminal = (
                            "FAILED_STOPPED"
                            if fault_mode == "PRECOMMIT_PERSISTENT"
                            else "COMPLETE"
                        )
                        self.assertEqual(
                            terminal["terminal_kind"], expected_terminal
                        )
                        expected_attempts = {
                            "PRECOMMIT_ONCE": 2,
                            "POSTCOMMIT": 1,
                            "PRECOMMIT_PERSISTENT": 2,
                        }[fault_mode]
                        self.assertEqual(
                            len(attempted_candidates), expected_attempts
                        )
                        self.assertTrue(
                            all(
                                candidate == attempted_candidates[0]
                                for candidate in attempted_candidates
                            )
                        )
                        launched_sample_ids = [
                            item["sample_id"] for item in launches
                        ]
                        expected_launch_count = (
                            {
                                "RUNNING": 0,
                                "ORDINARY_PASS": 1,
                                "FINAL_PENDING": len(
                                    EXPANDED_EXECUTION_SCHEDULE
                                ),
                            }[transition_mode]
                            if fault_mode == "PRECOMMIT_PERSISTENT"
                            else len(EXPANDED_EXECUTION_SCHEDULE)
                        )
                        self.assertEqual(
                            launched_sample_ids,
                            list(EXPANDED_EXECUTION_SCHEDULE[:expected_launch_count]),
                        )
                        self.assertEqual(
                            len(set(launched_sample_ids)),
                            len(launched_sample_ids),
                        )
                        self.assertEqual(
                            len({item["launch_token"] for item in launches}),
                            len(launches),
                        )
                        self.assertTrue(
                            all(
                                entry["start_count"] == 1
                                for entry in terminal["ledger"]["entries"]
                                if entry["state"] != "NOT_STARTED"
                            )
                        )
                        self.assertFalse(
                            consumption.COMPLETE_PATH.exists()
                            and consumption.FAILURE_PATH.exists()
                        )

    def test_outer_handler_uses_durable_ledger_not_stale_loop_locals(self) -> None:
        for mode in ("RUNNING", "ORDINARY_PASS", "FINAL_PENDING"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                launches: list[dict[str, Any]] = []
                with _synthetic_campaign(root, launches):
                    original_running = campaign.mark_sample_running
                    original_pass = campaign.mark_sample_pass

                    def committed_running_then_raise(
                        sequence_index: int,
                        sample_id: str,
                        selected_mode: str = mode,
                        mark_running: Any = original_running,
                    ) -> str:
                        token = mark_running(sequence_index, sample_id)
                        if selected_mode == "RUNNING" and sequence_index == 0:
                            raise OSError("synthetic error after RUNNING return")
                        return token

                    def committed_pass_then_raise(
                        sequence_index: int,
                        sample_id: str,
                        *,
                        launch_token: str,
                        manifest_sha256: str,
                        selected_mode: str = mode,
                        mark_pass: Any = original_pass,
                    ) -> dict[str, Any]:
                        ledger = mark_pass(
                            sequence_index,
                            sample_id,
                            launch_token=launch_token,
                            manifest_sha256=manifest_sha256,
                        )
                        if selected_mode == "ORDINARY_PASS" and sequence_index == 0:
                            raise OSError("synthetic error after ordinary PASS return")
                        if (
                            selected_mode == "FINAL_PENDING"
                            and sequence_index + 1
                            == len(EXPANDED_EXECUTION_SCHEDULE)
                        ):
                            raise OSError("synthetic error after final PASS return")
                        return ledger

                    with (
                        patch.object(
                            campaign,
                            "mark_sample_running",
                            side_effect=committed_running_then_raise,
                        ),
                        patch.object(
                            campaign,
                            "mark_sample_pass",
                            side_effect=committed_pass_then_raise,
                        ),
                        self.assertRaises(OSError),
                    ):
                        campaign.execute_campaign()

                    terminal = consumption.validate_terminal_execution_state()
                    self.assertEqual(terminal["terminal_kind"], "FAILED_STOPPED")
                    failure = terminal["failure"]
                    self.assertIsInstance(failure, Mapping)
                    expected_completed = {
                        "RUNNING": 0,
                        "ORDINARY_PASS": 1,
                        "FINAL_PENDING": len(EXPANDED_EXECUTION_SCHEDULE),
                    }[mode]
                    self.assertEqual(
                        len(failure["completed_samples"]), expected_completed
                    )
                    expected_status = (
                        "ALL_SAMPLES_PASS_PENDING_TERMINAL"
                        if mode == "FINAL_PENDING"
                        else "IN_PROGRESS"
                    )
                    self.assertEqual(
                        failure["observed_ledger_status"], expected_status
                    )
                    if mode == "RUNNING":
                        self.assertEqual(failure["active_sequence_index"], 0)
                        self.assertEqual(
                            failure["active_sample"],
                            EXPANDED_EXECUTION_SCHEDULE[0],
                        )
                        self.assertEqual(launches, [])
                    else:
                        self.assertIsNone(failure["active_sequence_index"])
                        self.assertIsNone(failure["active_sample"])
                    self.assertFalse(consumption.COMPLETE_PATH.exists())

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
                sample_id = EXPANDED_EXECUTION_SCHEDULE[0]
                token = consumption.mark_sample_running(0, sample_id)
                staging = Path(
                    tempfile.mkdtemp(
                        dir=consumption.RAW_ROOT,
                        prefix=f".{sample_id}.staging.synthetic.",
                    )
                )
                valid = consumption.validate_worker_launch(
                    sample_id=sample_id,
                    sequence_index=0,
                    launch_token=token,
                    orchestrator_pid=os.getpid(),
                    lock_path=lock_path,
                    staging_path=staging,
                )
                self.assertTrue(valid["gate_pass"])
                with self.assertRaises(FileExistsError):
                    consumption.validate_worker_launch(
                        sample_id=sample_id,
                        sequence_index=0,
                        launch_token=token,
                        orchestrator_pid=os.getpid(),
                        lock_path=lock_path,
                        staging_path=staging,
                    )
                with self.assertRaises(P0PerformanceError):
                    consumption.validate_worker_launch(
                        sample_id=sample_id,
                        sequence_index=0,
                        launch_token="manual-direct-call",
                        orchestrator_pid=os.getpid(),
                        lock_path=lock_path,
                        staging_path=staging,
                    )
                with self.assertRaises(P0PerformanceError):
                    consumption.validate_worker_launch(
                        sample_id=sample_id,
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
                    "schema_version": 2,
                    "status": "HF3D_P0P2_V2_FROZEN_SCHEDULE_EXECUTED_PENDING_ANALYSIS",
                    "gate_pass": True,
                    "authorization_child_commit": child_commit,
                    "completed_samples": list(EXPANDED_EXECUTION_SCHEDULE),
                    "completed_monotonic_ns": time.monotonic_ns(),
                }
                consumption.publish_execution_complete(completion)
                self.assertEqual(
                    consumption.validate_terminal_execution_state()["terminal_kind"],
                    "COMPLETE",
                )
                with self.assertRaises(P0PerformanceError):
                    consumption.mark_sample_running(0, EXPANDED_EXECUTION_SCHEDULE[0])
                with self.assertRaises(P0PerformanceError):
                    consumption.publish_execution_failure(
                        {
                            "schema_version": 2,
                            "status": "INVALID_AFTER_COMPLETE",
                            "gate_pass": False,
                        }
                    )
                self.assertTrue(
                    consumption.publish_execution_complete(completion)[
                        "recovered_existing"
                    ]
                )
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
                    "schema_version": 2,
                    "status": "HF3D_P0P2_V2_SYNTHETIC_FAILED_STOPPED",
                    "gate_pass": False,
                }
                consumption.mark_schedule_failed(
                    sequence_index=0,
                    sample_id=EXPANDED_EXECUTION_SCHEDULE[0],
                    failure=failure,
                )
                consumption.publish_execution_failure(failure)
                self.assertEqual(
                    consumption.validate_terminal_execution_state()["terminal_kind"],
                    "FAILED_STOPPED",
                )
                with self.assertRaises(P0PerformanceError):
                    consumption.mark_sample_running(1, EXPANDED_EXECUTION_SCHEDULE[1])
                with self.assertRaises(P0PerformanceError):
                    consumption.publish_execution_complete(
                        {
                            "schema_version": 2,
                            "status": "HF3D_P0P2_V2_FROZEN_SCHEDULE_EXECUTED_PENDING_ANALYSIS",
                            "gate_pass": True,
                            "authorization_child_commit": "c" * 40,
                            "completed_samples": list(EXPANDED_EXECUTION_SCHEDULE),
                            "completed_monotonic_ns": time.monotonic_ns(),
                        }
                    )
                self.assertTrue(
                    consumption.publish_execution_failure(failure)[
                        "recovered_existing"
                    ]
                )
                with self.assertRaises(P0PerformanceError):
                    consumption.create_claim_and_ledger(**inputs)

    def test_terminal_fault_injection_never_leaves_false_complete(self) -> None:
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
                last_token = ""
                for index, sample_id in enumerate(EXPANDED_EXECUTION_SCHEDULE):
                    token = consumption.mark_sample_running(index, sample_id)
                    if index + 1 == len(EXPANDED_EXECUTION_SCHEDULE):
                        last_token = token
                        break
                    consumption.mark_sample_pass(
                        index,
                        sample_id,
                        launch_token=token,
                        manifest_sha256="a" * 64,
                    )
                original_publish = consumption._publish_ledger
                failed_once = False

                def fail_last_pass_once(ledger: Mapping[str, Any]) -> None:
                    nonlocal failed_once
                    if (
                        ledger.get("status")
                        == "ALL_SAMPLES_PASS_PENDING_TERMINAL"
                        and not failed_once
                    ):
                        failed_once = True
                        raise OSError("synthetic final ledger write failure")
                    original_publish(ledger)

                with patch.object(
                    consumption,
                    "_publish_ledger",
                    side_effect=fail_last_pass_once,
                ):
                    consumption.mark_sample_pass(
                        len(EXPANDED_EXECUTION_SCHEDULE) - 1,
                        EXPANDED_EXECUTION_SCHEDULE[-1],
                        launch_token=last_token,
                        manifest_sha256="a" * 64,
                    )
                self.assertTrue(failed_once)
                child = inputs["authorization"]["observed_parent_ticket_binding"][
                    "authorization_child_commit"
                ]
                completion = {
                    "schema_version": 2,
                    "status": "HF3D_P0P2_V2_FROZEN_SCHEDULE_EXECUTED_PENDING_ANALYSIS",
                    "gate_pass": True,
                    "authorization_child_commit": child,
                    "completed_samples": list(EXPANDED_EXECUTION_SCHEDULE),
                    "completed_monotonic_ns": time.monotonic_ns(),
                }
                consumption.publish_execution_complete(completion)
                terminal = consumption.validate_terminal_execution_state()
                self.assertEqual(terminal["terminal_kind"], "COMPLETE")
                self.assertFalse(consumption.FAILURE_PATH.exists())

        for mode in ("before_create", "partial_create"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
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
                    child = inputs["authorization"][
                        "observed_parent_ticket_binding"
                    ]["authorization_child_commit"]
                    completion = {
                        "schema_version": 2,
                        "status": "HF3D_P0P2_V2_FROZEN_SCHEDULE_EXECUTED_PENDING_ANALYSIS",
                        "gate_pass": True,
                        "authorization_child_commit": child,
                        "completed_samples": list(EXPANDED_EXECUTION_SCHEDULE),
                        "completed_monotonic_ns": time.monotonic_ns(),
                    }
                    original_create = consumption.create_exclusive_json

                    def fail_completion(
                        path: Path,
                        value: Mapping[str, Any],
                        test_mode: str = mode,
                        create: Any = original_create,
                    ) -> dict[str, Any]:
                        if path == consumption.COMPLETE_PATH:
                            if test_mode == "partial_create":
                                path.write_text("{partial", encoding="utf-8")
                            raise OSError(f"synthetic completion {test_mode}")
                        return create(path, value)

                    with (
                        patch.object(
                            consumption,
                            "create_exclusive_json",
                            side_effect=fail_completion,
                        ),
                        self.assertRaises(OSError),
                    ):
                        consumption.publish_execution_complete(completion)
                    failure = {
                        "schema_version": 2,
                        "status": "HF3D_P0P2_V2_COMPLETION_WRITE_FAILED_STOPPED",
                        "gate_pass": False,
                    }
                    consumption.mark_schedule_failed(
                        sequence_index=None,
                        sample_id=None,
                        failure=failure,
                    )
                    consumption.publish_execution_failure(failure)
                    terminal = consumption.validate_terminal_execution_state()
                    self.assertEqual(terminal["terminal_kind"], "FAILED_STOPPED")
                    self.assertFalse(consumption.COMPLETE_PATH.exists())
                    if mode == "partial_create":
                        self.assertEqual(
                            len(list(consumption.RAW_ROOT.glob("execution_complete_aborted_*"))),
                            1,
                        )

    def test_partial_failure_record_is_quarantined_and_retried(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "artifacts").mkdir()
            lock_path = root / "campaign.lock"
            with (
                _runtime_paths(root / "artifacts"),
                ExclusiveCampaignLock(lock_path) as lock,
            ):
                consumption.create_claim_and_ledger(**_claim_inputs(lock.identity))
                sample_id = EXPANDED_EXECUTION_SCHEDULE[0]
                consumption.mark_sample_running(0, sample_id)
                failure = {
                    "schema_version": 2,
                    "status": "HF3D_P0P2_V2_SYNTHETIC_STORAGE_FAILED_STOPPED",
                    "gate_pass": False,
                }
                consumption.mark_schedule_failed(
                    sequence_index=0,
                    sample_id=sample_id,
                    failure=failure,
                )
                original = consumption.create_exclusive_json
                injected = False

                def partial_once(
                    path: Path, value: Mapping[str, Any]
                ) -> dict[str, Any]:
                    nonlocal injected
                    if path == consumption.FAILURE_PATH and not injected:
                        injected = True
                        path.write_text("{partial", encoding="utf-8")
                        raise OSError("synthetic partial failure write")
                    return original(path, value)

                with patch.object(
                    consumption,
                    "create_exclusive_json",
                    side_effect=partial_once,
                ):
                    consumption.publish_execution_failure(failure)
                terminal = consumption.validate_terminal_execution_state()
                self.assertEqual(terminal["terminal_kind"], "FAILED_STOPPED")
                self.assertEqual(
                    len(list(consumption.RAW_ROOT.glob("execution_failure_aborted_*"))),
                    1,
                )

    def test_failed_ledger_and_failure_precreate_faults_are_retried(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "artifacts").mkdir()
            lock_path = root / "campaign.lock"
            with (
                _runtime_paths(root / "artifacts"),
                ExclusiveCampaignLock(lock_path) as lock,
            ):
                consumption.create_claim_and_ledger(**_claim_inputs(lock.identity))
                sample_id = EXPANDED_EXECUTION_SCHEDULE[0]
                consumption.mark_sample_running(0, sample_id)
                failure = {
                    "schema_version": 2,
                    "status": "HF3D_P0P2_V2_RETRIED_STORAGE_FAILED_STOPPED",
                    "gate_pass": False,
                }
                original_publish = consumption._publish_ledger
                ledger_injected = False

                def fail_ledger_once(ledger: Mapping[str, Any]) -> None:
                    nonlocal ledger_injected
                    if ledger.get("status") == "FAILED_STOPPED" and not ledger_injected:
                        ledger_injected = True
                        raise OSError("synthetic failed-ledger precreate fault")
                    original_publish(ledger)

                with patch.object(
                    consumption,
                    "_publish_ledger",
                    side_effect=fail_ledger_once,
                ):
                    consumption.mark_schedule_failed(
                        sequence_index=0,
                        sample_id=sample_id,
                        failure=failure,
                    )
                original_create = consumption.create_exclusive_json
                failure_injected = False

                def fail_failure_once(
                    path: Path, value: Mapping[str, Any]
                ) -> dict[str, Any]:
                    nonlocal failure_injected
                    if path == consumption.FAILURE_PATH and not failure_injected:
                        failure_injected = True
                        raise OSError("synthetic failure precreate fault")
                    return original_create(path, value)

                with patch.object(
                    consumption,
                    "create_exclusive_json",
                    side_effect=fail_failure_once,
                ):
                    consumption.publish_execution_failure(failure)
                self.assertEqual(
                    consumption.validate_terminal_execution_state()["terminal_kind"],
                    "FAILED_STOPPED",
                )

if __name__ == "__main__":
    unittest.main()
