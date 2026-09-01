from __future__ import annotations

import copy
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parents[1]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from p0_performance_contract import REPOSITORY_ROOT
from p0p_authorization import REQUIRED_IMPLEMENTATION_RELATIVE_PATHS
from review_tools.generate_p0p_review_binding import (
    EXPECTED_ZERO_COUNTERS,
    MATERIAL_STATUS,
    REVIEWED_RUNTIME_COMMIT,
    ReviewBindingError,
    build_material,
    check_material_outputs,
    git_bound_file_record,
    material_json_bytes,
    material_markdown,
    validate_material,
    write_material_outputs,
)


def _run(repository: Path, *arguments: str) -> bytes:
    completed = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        raise AssertionError(
            completed.stderr.decode("utf-8", errors="replace")
            or completed.stdout.decode("utf-8", errors="replace")
        )
    return completed.stdout


class ProductionReviewMaterialTests(unittest.TestCase):
    material: dict[str, Any]

    @classmethod
    def setUpClass(cls) -> None:
        cls.material = build_material()

    def candidate(self) -> dict[str, Any]:
        return copy.deepcopy(self.material)

    def test_canonical_material_validates_and_is_non_authorizing(self) -> None:
        validated = validate_material(self.material)
        self.assertEqual(validated["status"], MATERIAL_STATUS)
        state = validated["authorization_state"]
        self.assertTrue(state["material_is_non_authorizing"])
        self.assertTrue(state["actual_pro_ticket_absent"])
        self.assertTrue(state["authorized_grant_absent"])
        self.assertFalse(state["dynamic_samples_authorized"])
        self.assertEqual(state["zero_step_counters"], EXPECTED_ZERO_COUNTERS)

    def test_required_implementation_universe_is_exact_and_ordered(self) -> None:
        records = self.material["bindings"]["implementation_files"]
        paths = [record["path"] for record in records]
        self.assertEqual(paths, sorted(REQUIRED_IMPLEMENTATION_RELATIVE_PATHS))
        self.assertEqual(len(paths), len(set(paths)))

    def test_missing_required_implementation_record_fails(self) -> None:
        candidate = self.candidate()
        candidate["bindings"]["implementation_files"].pop()
        with self.assertRaisesRegex(ReviewBindingError, "universe differs"):
            validate_material(candidate)

    def test_duplicate_required_implementation_record_fails(self) -> None:
        candidate = self.candidate()
        records = candidate["bindings"]["implementation_files"]
        records.append(copy.deepcopy(records[0]))
        with self.assertRaisesRegex(ReviewBindingError, "duplicates"):
            validate_material(candidate)

    def test_extra_required_implementation_record_fails(self) -> None:
        candidate = self.candidate()
        extra = copy.deepcopy(candidate["bindings"]["implementation_files"][0])
        extra["path"] = "90_候选工作区/current/17_HF3D性能优化/extra.py"
        candidate["bindings"]["implementation_files"].append(extra)
        with self.assertRaisesRegex(ReviewBindingError, "universe differs"):
            validate_material(candidate)

    def test_path_mode_blob_hash_and_length_drift_fail(self) -> None:
        first_mode = self.material["bindings"]["implementation_files"][0]["mode"]
        mutations = {
            "path": "90_候选工作区/current/17_HF3D性能优化/not-the-file.py",
            "mode": "100644" if first_mode == "100755" else "100755",
            "git_blob": "0" * 40,
            "sha256": "0" * 64,
            "byte_length": 1,
        }
        for field, value in mutations.items():
            with self.subTest(field=field):
                candidate = self.candidate()
                candidate["bindings"]["implementation_files"][0][field] = value
                with self.assertRaises(ReviewBindingError):
                    validate_material(candidate)

    def test_special_binding_drift_fails(self) -> None:
        for name in (
            "static_audit",
            "contract",
            "capability_manifest",
            "denied_authorization",
        ):
            with self.subTest(name=name):
                candidate = self.candidate()
                candidate["bindings"][name]["sha256"] = "f" * 64
                with self.assertRaisesRegex(
                    ReviewBindingError,
                    "deterministic Git-derived object",
                ):
                    validate_material(candidate)

    def test_fixed_schedule_order_and_field_drift_fail(self) -> None:
        candidate = self.candidate()
        items = candidate["fixed_schedule"]["ordered_items"]
        items[0], items[1] = items[1], items[0]
        with self.assertRaisesRegex(ReviewBindingError, "fixed schedule"):
            validate_material(candidate)

        candidate = self.candidate()
        candidate["fixed_schedule"]["ordered_items"][0]["duration_s"] = 1.0
        with self.assertRaisesRegex(ReviewBindingError, "fixed schedule"):
            validate_material(candidate)

    def test_self_reference_field_fails(self) -> None:
        candidate = self.candidate()
        candidate["self_commit_hash"] = "0" * 40
        with self.assertRaisesRegex(ReviewBindingError, "forbidden self/future"):
            validate_material(candidate)

    def test_containing_commit_and_tree_values_are_omitted(self) -> None:
        commit = _run(REPOSITORY_ROOT, "rev-parse", "HEAD").decode("ascii").strip()
        tree = _run(REPOSITORY_ROOT, "rev-parse", "HEAD^{tree}").decode("ascii").strip()
        payload = material_json_bytes(self.material)
        self.assertNotIn(commit.encode("ascii"), payload)
        self.assertNotIn(tree.encode("ascii"), payload)

        for label, value in (
            ("unexpected_commit_value", commit),
            ("unexpected_tree_value", tree),
        ):
            with self.subTest(label=label):
                candidate = self.candidate()
                candidate["generation_invariants"][label] = value
                with self.assertRaisesRegex(
                    ReviewBindingError,
                    "commit/tree that currently contains it",
                ):
                    validate_material(candidate)

    def test_formal_source_identity_drift_fails(self) -> None:
        candidate = self.candidate()
        candidate["formal_source_identity"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(ReviewBindingError, "formal source"):
            validate_material(candidate)

    def test_dynamic_authorization_or_zero_counter_drift_fails(self) -> None:
        candidate = self.candidate()
        candidate["authorization_state"]["dynamic_samples_authorized"] = True
        with self.assertRaisesRegex(ReviewBindingError, "non-authorizing"):
            validate_material(candidate)

        candidate = self.candidate()
        candidate["authorization_state"]["zero_step_counters"][
            "hf3d_time_steps_advanced"
        ] = 1
        with self.assertRaisesRegex(ReviewBindingError, "non-authorizing"):
            validate_material(candidate)

    def test_reviewed_runtime_reference_is_exact(self) -> None:
        self.assertEqual(
            self.material["reviewed_runtime_reference"]["commit_oid"],
            REVIEWED_RUNTIME_COMMIT,
        )
        candidate = self.candidate()
        candidate["reviewed_runtime_reference"]["commit_oid"] = "0" * 40
        with self.assertRaisesRegex(
            ReviewBindingError,
            "deterministic Git-derived object",
        ):
            validate_material(candidate)

    def test_regeneration_and_rendering_are_byte_identical(self) -> None:
        first = build_material()
        second = build_material()
        self.assertEqual(material_json_bytes(first), material_json_bytes(second))
        self.assertEqual(material_markdown(first), material_markdown(second))

    def test_write_then_check_is_byte_identical(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            json_path = root / "material.json"
            markdown_path = root / "material.md"
            write_material_outputs(
                self.material,
                json_path=json_path,
                markdown_path=markdown_path,
            )
            first_json = json_path.read_bytes()
            first_markdown = markdown_path.read_bytes()
            write_material_outputs(
                self.material,
                json_path=json_path,
                markdown_path=markdown_path,
            )
            self.assertEqual(first_json, json_path.read_bytes())
            self.assertEqual(first_markdown, markdown_path.read_bytes())
            check_material_outputs(
                self.material,
                json_path=json_path,
                markdown_path=markdown_path,
            )


class GitObjectKindNegativeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.repository = Path(self.temporary.name)
        _run(self.repository, "init", "-q")
        _run(self.repository, "config", "user.name", "P0P Review Test")
        _run(self.repository, "config", "user.email", "p0p-review@example.invalid")
        (self.repository / "regular.txt").write_text("regular\n", encoding="utf-8")
        os.symlink("regular.txt", self.repository / "symlink.txt")
        _run(self.repository, "add", "regular.txt", "symlink.txt")
        _run(self.repository, "commit", "-q", "-m", "regular and symlink")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_regular_blob_is_accepted_but_symlink_is_rejected(self) -> None:
        record = git_bound_file_record(
            self.repository,
            "HEAD",
            "regular.txt",
        )
        self.assertEqual(record["mode"], "100644")
        with self.assertRaisesRegex(ReviewBindingError, "not an allowed regular"):
            git_bound_file_record(
                self.repository,
                "HEAD",
                "symlink.txt",
                verify_worktree=False,
            )

    def test_gitlink_submodule_mode_is_rejected(self) -> None:
        head = _run(self.repository, "rev-parse", "HEAD").decode("ascii").strip()
        _run(
            self.repository,
            "update-index",
            "--add",
            "--cacheinfo",
            f"160000,{head},synthetic-submodule",
        )
        _run(self.repository, "commit", "-q", "-m", "add gitlink")
        with self.assertRaisesRegex(ReviewBindingError, "not an allowed regular"):
            git_bound_file_record(
                self.repository,
                "HEAD",
                "synthetic-submodule",
                verify_worktree=False,
            )


if __name__ == "__main__":
    unittest.main()
