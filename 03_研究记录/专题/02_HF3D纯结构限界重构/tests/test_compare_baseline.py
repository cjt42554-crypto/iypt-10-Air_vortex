"""Failure-closure tests for the strict HF3D baseline comparator."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np


TEST_ROOT = Path(__file__).resolve().parent.parent
if str(TEST_ROOT) not in sys.path:
    sys.path.insert(0, str(TEST_ROOT))

from capture_baseline import FORMAT_VERSION, array_sha256
from compare_baseline import compare_captures


class BaselineComparatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _base_manifest(archive_name: str) -> dict[str, object]:
        array = np.asarray([0.0, 1.0], dtype=np.float64)
        return {
            "format_version": FORMAT_VERSION,
            "capture_label": "test",
            "requested_device": "cpu",
            "selected_device": "cpu",
            "effective_config": {"backend": "high_fidelity"},
            "effective_config_sha256": "0" * 64,
            "environment": {"python": "test"},
            "provenance": {
                "git_head": "1" * 40,
                "source_tree": {"value": "2" * 64},
            },
            "array_archive": archive_name,
            "arrays": [
                {
                    "storage_key": "time_s",
                    "category": "time_s",
                    "logical_name": "time_s",
                    "dtype": array.dtype.str,
                    "shape": list(array.shape),
                    "sha256": array_sha256(array),
                }
            ],
            "result": {
                "backend": "high_fidelity",
                "status": "completed",
                "observable_keys": [],
                "field_keys": [],
                "events": [],
                "metadata": {"diagnostics": {"completed_steps": 1}},
            },
            "progress": [],
        }

    def _write_capture(
        self,
        stem: str,
        *,
        array: np.ndarray | None = None,
        mutate_manifest=None,
    ) -> Path:
        value = (
            np.asarray([0.0, 1.0], dtype=np.float64)
            if array is None
            else np.asarray(array)
        )
        archive = self.root / f"{stem}.npz"
        np.savez_compressed(archive, time_s=value)
        manifest = self._base_manifest(archive.name)
        manifest["arrays"][0]["dtype"] = value.dtype.str
        manifest["arrays"][0]["shape"] = list(value.shape)
        manifest["arrays"][0]["sha256"] = array_sha256(value)
        if mutate_manifest is not None:
            mutate_manifest(manifest)
        path = self.root / f"{stem}.json"
        path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
        return path

    def test_equal_capture_passes_with_identity_whitelist(self) -> None:
        before = self._write_capture("before")

        def identity_change(manifest):
            manifest["capture_label"] = "after"
            manifest["provenance"]["git_head"] = "3" * 40
            manifest["provenance"]["source_tree"]["value"] = "4" * 64

        after = self._write_capture("after", mutate_manifest=identity_change)
        report = compare_captures(before, after)
        self.assertTrue(report["pass"])
        self.assertEqual(report["arrays_compared"], 1)

    def test_missing_array_fails_closed(self) -> None:
        before = self._write_capture("before")

        def remove_array(manifest):
            manifest["arrays"] = []

        after = self._write_capture("after", mutate_manifest=remove_array)
        report = compare_captures(before, after)
        self.assertFalse(report["pass"])
        self.assertEqual(
            report["first_difference"]["kind"],
            "unreadable_or_invalid_evidence",
        )

    def test_shape_or_dtype_change_fails_closed(self) -> None:
        before = self._write_capture("before")
        after = self._write_capture(
            "after", array=np.asarray([[0.0, 1.0]], dtype=np.float32)
        )
        report = compare_captures(before, after)
        self.assertFalse(report["pass"])
        self.assertEqual(report["first_difference"]["kind"], "array_contract")

    def test_event_change_fails_before_array_comparison(self) -> None:
        before = self._write_capture("before")

        def add_event(manifest):
            manifest["result"]["events"] = [
                {"time_s": 0.0, "kind": "changed", "message": "", "data": {}}
            ]

        after = self._write_capture("after", mutate_manifest=add_event)
        report = compare_captures(before, after)
        self.assertFalse(report["pass"])
        self.assertEqual(report["first_difference"]["kind"], "json_length")
        self.assertEqual(report["arrays_compared"], 0)

    def test_first_array_value_difference_is_reported_exactly(self) -> None:
        before = self._write_capture("before")
        after = self._write_capture(
            "after", array=np.asarray([0.0, 1.0 + np.finfo(float).eps])
        )
        report = compare_captures(before, after)
        self.assertFalse(report["pass"])
        self.assertEqual(report["first_difference"]["kind"], "array_value")
        self.assertIn("time_s", report["first_difference"]["path"])


if __name__ == "__main__":
    unittest.main()
