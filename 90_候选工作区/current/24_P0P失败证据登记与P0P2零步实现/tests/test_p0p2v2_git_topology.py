from __future__ import annotations

import copy
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from p0p2v2_authorization import validate_authorized_grant, validate_ticket
from p0p2v2_contract import CONTRACT_ID, P0PerformanceError
from test_p0p2v2_authorization_and_consumption import (
    SyntheticAuthorizationRepository,
    _run,
    _text,
)


class CompleteGitTopologyTests(unittest.TestCase):
    def test_positive_i2_p2_c2_fetch_symbolic_main_and_nul_safe_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            synthetic = SyntheticAuthorizationRepository(Path(temporary))
            report = synthetic.audit()
            self.assertTrue(report["gate_pass"])
            self.assertEqual(
                _text(synthetic.repository, "symbolic-ref", "HEAD"),
                "refs/heads/main",
            )
            self.assertTrue(
                any(
                    "\n" in path
                    for record in report["nul_safe_review_delta"]
                    for path in record["paths"]
                )
            )

    def test_merge_p2_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            synthetic = SyntheticAuthorizationRepository(
                Path(temporary), parent_merge=True
            )
            with self.assertRaises(P0PerformanceError):
                synthetic.audit()

    def test_merge_c2_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            synthetic = SyntheticAuthorizationRepository(Path(temporary))
            _run(synthetic.repository, "checkout", "-b", "synthetic-c2-side")
            _run(
                synthetic.repository,
                "commit",
                "--allow-empty",
                "-m",
                "side child",
            )
            _run(synthetic.repository, "checkout", "main")
            _run(
                synthetic.repository,
                "merge",
                "--no-ff",
                "synthetic-c2-side",
                "-m",
                "merge child",
            )
            _run(synthetic.repository, "push", "origin", "main")
            with self.assertRaises(P0PerformanceError):
                synthetic.audit()

    def test_intermediate_parent_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            synthetic = SyntheticAuthorizationRepository(
                Path(temporary), intermediate_parent=True
            )
            with self.assertRaises(P0PerformanceError):
                synthetic.audit()

    def test_fetch_exposes_stale_local_main(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            synthetic = SyntheticAuthorizationRepository(Path(temporary))
            _run(
                synthetic.repository,
                "commit",
                "--allow-empty",
                "-m",
                "remote advanced",
            )
            remote_head = _text(synthetic.repository, "rev-parse", "HEAD")
            _run(synthetic.repository, "push", "origin", "main")
            self.assertNotEqual(remote_head, synthetic.child)
            _run(
                synthetic.repository,
                "update-ref",
                "refs/heads/main",
                synthetic.child,
            )
            with self.assertRaises(P0PerformanceError):
                synthetic.audit()

    def test_wrong_symbolic_branch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            synthetic = SyntheticAuthorizationRepository(Path(temporary))
            _run(synthetic.repository, "checkout", "-b", "not-main")
            with self.assertRaises(P0PerformanceError):
                synthetic.audit()

    def test_extra_review_and_child_paths_are_rejected(self) -> None:
        for option in ("extra_review_path", "extra_child_path"):
            with self.subTest(option=option), tempfile.TemporaryDirectory() as temporary:
                if option == "extra_review_path":
                    synthetic = SyntheticAuthorizationRepository(
                        Path(temporary), extra_review_path=True
                    )
                else:
                    synthetic = SyntheticAuthorizationRepository(
                        Path(temporary), extra_child_path=True
                    )
                with self.assertRaises(P0PerformanceError):
                    synthetic.audit()

    def test_rename_copy_mode_symlink_and_submodule_are_rejected(self) -> None:
        for kind in ("rename", "copy", "mode", "symlink", "submodule"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary:
                synthetic = SyntheticAuthorizationRepository(
                    Path(temporary), child_kind=kind
                )
                with self.assertRaises(P0PerformanceError):
                    synthetic.audit()

    def test_wrong_denied_and_authorized_blobs_are_rejected(self) -> None:
        for option in ("wrong_denied_blob", "wrong_authorized_blob"):
            with self.subTest(option=option), tempfile.TemporaryDirectory() as temporary:
                if option == "wrong_denied_blob":
                    synthetic = SyntheticAuthorizationRepository(
                        Path(temporary), wrong_denied_blob=True
                    )
                else:
                    synthetic = SyntheticAuthorizationRepository(
                        Path(temporary), wrong_authorized_blob=True
                    )
                with self.assertRaises(P0PerformanceError):
                    synthetic.audit()

    def test_missing_and_drifted_reviewed_files_are_rejected(self) -> None:
        for kind in ("missing", "drifted"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary:
                synthetic = SyntheticAuthorizationRepository(
                    Path(temporary), reviewed_binding_kind=kind
                )
                with self.assertRaises(P0PerformanceError):
                    synthetic.audit()

    def test_nul_in_ticket_path_is_rejected_before_git_interpretation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            synthetic = SyntheticAuthorizationRepository(Path(temporary))
            ticket = copy.deepcopy(synthetic.audit()["ticket"])
            old = ticket["ruling_path"]
            ticket["ruling_path"] = "合成/坏\0路径.md"
            policy = ticket["review_parent_policy"]
            policy["ruling_path"] = ticket["ruling_path"]
            policy["allowed_review_paths"] = [
                ticket["ruling_path"] if path == old else path
                for path in policy["allowed_review_paths"]
            ]
            with self.assertRaises(P0PerformanceError):
                validate_ticket(ticket)

    def test_contract_and_grant_namespace_drift_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            synthetic = SyntheticAuthorizationRepository(Path(temporary))
            wrong_contract = {**synthetic.grant, "contract_id": "WRONG"}
            wrong_nonce = {**synthetic.grant, "grant_nonce": "wrong-namespace"}
            self.assertEqual(synthetic.grant["contract_id"], CONTRACT_ID)
            with self.assertRaises(P0PerformanceError):
                validate_authorized_grant(wrong_contract)
            with self.assertRaises(P0PerformanceError):
                validate_authorized_grant(wrong_nonce)


if __name__ == "__main__":
    unittest.main()
