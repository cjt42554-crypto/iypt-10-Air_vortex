from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[1]
if str(WORKSPACE) not in sys.path:
    sys.path.insert(0, str(WORKSPACE))

import register_v1_failure_evidence as registration


class TreeSafetyTests(unittest.TestCase):
    def test_copy_has_identical_canonical_byte_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            source = base / "source"
            source.mkdir()
            (source / "nested").mkdir()
            (source / "a.json").write_text('{"a": 1}\n', encoding="utf-8")
            (source / "nested/empty.jsonl").write_bytes(b"")
            before = registration.scan_tree(source)
            destination = base / "copy"
            registration.copy_regular_tree(source, destination, before)
            after = registration.scan_tree(destination)
            self.assertTrue(registration.stable_byte_identity(before, after))
            self.assertEqual(
                before["canonical_tree_sha256"], after["canonical_tree_sha256"]
            )

    def test_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "tree"
            root.mkdir()
            (root / "target").write_bytes(b"x")
            (root / "link").symlink_to("target")
            with self.assertRaises(registration.EvidenceRegistrationError):
                registration.scan_tree(root)

    def test_hardlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "tree"
            root.mkdir()
            first = root / "first"
            first.write_bytes(b"x")
            os.link(first, root / "second")
            with self.assertRaises(registration.EvidenceRegistrationError):
                registration.scan_tree(root)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO is not supported")
    def test_fifo_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "tree"
            root.mkdir()
            os.mkfifo(root / "fifo")
            with self.assertRaises(registration.EvidenceRegistrationError):
                registration.scan_tree(root)

    def test_duplicate_json_key_is_rejected(self) -> None:
        with self.assertRaises(registration.EvidenceRegistrationError):
            registration.strict_json_bytes(b'{"a":1,"a":2}', label="duplicate")


class V1SemanticTests(unittest.TestCase):
    def test_live_source_has_exact_terminal_semantics(self) -> None:
        tree = registration.scan_tree(registration.SOURCE_ROOT)
        report = registration.audit_v1_semantics(registration.SOURCE_ROOT, tree)
        self.assertTrue(report["gate_pass"])
        self.assertEqual(report["derived_fact"]["hf3d_time_steps_advanced"], 0)
        self.assertFalse(
            report["derived_fact"]["measured_performance_result_available"]
        )

    def test_registered_snapshot_remains_exact(self) -> None:
        report = registration.check_registration()
        self.assertTrue(report["gate_pass"])
        self.assertEqual(
            report["source_canonical_tree_sha256"],
            report["snapshot_canonical_tree_sha256"],
        )

    def test_mutated_terminal_ledger_fails_semantic_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "copy"
            source_tree = registration.scan_tree(registration.SOURCE_ROOT)
            registration.copy_regular_tree(
                registration.SOURCE_ROOT, destination, source_tree
            )
            ledger_path = destination / "schedule_ledger.json"
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
            ledger["entries"][0]["state"] = "NOT_STARTED"
            ledger_path.write_text(
                json.dumps(ledger, sort_keys=True) + "\n", encoding="utf-8"
            )
            tree = registration.scan_tree(destination)
            report = registration.audit_v1_semantics(destination, tree)
            self.assertFalse(report["gate_pass"])
            self.assertFalse(report["gates"]["warmup_b1_terminal_start_exact"])

    def test_completion_injection_fails_semantic_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "copy"
            source_tree = registration.scan_tree(registration.SOURCE_ROOT)
            registration.copy_regular_tree(
                registration.SOURCE_ROOT, destination, source_tree
            )
            (destination / "execution_complete.json").write_text(
                "{}\n", encoding="utf-8"
            )
            tree = registration.scan_tree(destination)
            report = registration.audit_v1_semantics(destination, tree)
            self.assertFalse(report["gate_pass"])
            self.assertFalse(
                report["gates"]["completion_and_dynamic_result_files_absent"]
            )

    def test_byte_mutation_breaks_copy_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "copy"
            source_tree = registration.scan_tree(registration.SOURCE_ROOT)
            registration.copy_regular_tree(
                registration.SOURCE_ROOT, destination, source_tree
            )
            with (destination / "WARMUP_B1/worker.stdout.jsonl").open("ab") as stream:
                stream.write(b"{}\n")
            mutated = registration.scan_tree(destination)
            self.assertFalse(registration.stable_byte_identity(source_tree, mutated))

    def test_copy_does_not_modify_source_identity(self) -> None:
        before = registration.scan_tree(registration.SOURCE_ROOT)
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "copy"
            registration.copy_regular_tree(
                registration.SOURCE_ROOT, destination, before
            )
        after = registration.scan_tree(registration.SOURCE_ROOT)
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
