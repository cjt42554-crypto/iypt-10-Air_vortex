from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

import numpy as np

SCRIPT_PATH = Path(__file__).resolve().parents[1] / (
    "audit_d3r1_divergence_diagnostics.py"
)
SPEC = importlib.util.spec_from_file_location("p0_divergence_audit", SCRIPT_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load P0-D audit module")
audit: Any = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


class P0DivergenceAuditTests(unittest.TestCase):
    def test_extracts_only_leading_fields_object(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            path = Path(directory) / "result.json"
            path.write_text(
                '{"schema":1,"arrays":{"fields":{'
                '"diagnostic":{"dtype":"float64","npz_key":"field_0001"}'
                '},"observables":{"secret":{"npz_key":"observable_0001"}}}'
                ',"unparsed_tail":INVALID}',
                encoding="utf-8",
            )
            self.assertEqual(
                audit._extract_fields_metadata(path),
                {
                    "diagnostic": {
                        "dtype": "float64",
                        "npz_key": "field_0001",
                    }
                },
            )

    def test_fields_extractor_rejects_duplicate_and_late_markers(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            root = Path(directory)
            duplicate = root / "duplicate.json"
            duplicate.write_text(
                '{"arrays":{"fields":{"x":{},"x":{}}}}', encoding="utf-8"
            )
            with self.assertRaises(audit.P0DiagnosticError):
                audit._extract_fields_metadata(duplicate)

            late = root / "late.json"
            late.write_text(
                '{"padding":"' + ("x" * 66_000) + '","arrays":{"fields":{}}}',
                encoding="utf-8",
            )
            with self.assertRaises(audit.P0DiagnosticError):
                audit._extract_fields_metadata(late)

    def test_validate_series_is_float64_rank_one_and_finite(self) -> None:
        accepted = np.asarray([1.0, 2.0], dtype=np.float64)
        loaded = audit._validate_series("accepted", accepted)
        np.testing.assert_array_equal(loaded, accepted)
        self.assertIsNot(loaded, accepted)
        for rejected in (
            np.asarray([1.0], dtype=np.float32),
            np.asarray([[1.0]], dtype=np.float64),
            np.asarray([np.nan], dtype=np.float64),
        ):
            with self.assertRaises(audit.P0DiagnosticError):
                audit._validate_series("rejected", rejected)

    def test_window_statistics_and_robust_outlier_are_frozen(self) -> None:
        time_s = np.asarray([0.0, 1.0, 2.0], dtype=np.float64)
        values = np.asarray([1.0, 3.0, 5.0], dtype=np.float64)
        statistics = audit._window_statistics(time_s, values)
        self.assertEqual(statistics["count"], 3)
        self.assertEqual(statistics["minimum"], 1.0)
        self.assertEqual(statistics["maximum"], 5.0)
        self.assertEqual(statistics["ordinary_least_squares_slope_per_second"], 2.0)
        unavailable = audit._robust_outlier(
            100.0, np.ones(31, dtype=np.float64), floor=1.0
        )
        self.assertIsNone(unavailable["flag"])
        flagged = audit._robust_outlier(3.0, np.ones(32, dtype=np.float64), floor=1.0)
        self.assertTrue(flagged["flag"])
        self.assertEqual(flagged["threshold"], 2.0)

    def test_completed_archive_loads_only_explicitly_mapped_members(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            root = Path(directory) / "runs"
            case_root = root / "cases" / "G6_DT0"
            archive_root = case_root / "archive"
            archive_root.mkdir(parents=True)
            summary = {
                "case_id": "G6_DT0",
                "status": "D3_CASE_COMPLETE_ALL_HARD_GATES_PASS",
                "gate_pass": True,
            }
            (case_root / "case_summary.json").write_text(
                json.dumps(summary), encoding="utf-8"
            )
            (archive_root / "result.json").write_text(
                json.dumps(
                    {
                        "arrays": {
                            "fields": {
                                "diagnostic_a": {
                                    "dtype": "float64",
                                    "npz_key": "field_0001",
                                },
                                "diagnostic_b": {
                                    "dtype": "float64",
                                    "npz_key": "field_0002",
                                },
                                "state_secret": {
                                    "dtype": "object",
                                    "npz_key": "field_9999",
                                },
                            },
                            "observables": {"secret": {"npz_key": "observable_0001"}},
                        }
                    }
                ),
                encoding="utf-8",
            )
            np.savez(
                archive_root / "arrays.npz",
                field_0001=np.asarray([1.0, 2.0], dtype=np.float64),
                field_0002=np.asarray([3.0, 4.0], dtype=np.float64),
                field_9999=np.asarray([object()], dtype=object),
                observable_0001=np.asarray([object()], dtype=object),
            )
            original_raw_root = audit.RAW_ROOT
            try:
                audit.RAW_ROOT = root
                access: dict[str, Any] = {}
                loaded, loaded_summary = audit._load_completed_case(
                    "G6_DT0", ("diagnostic_a", "diagnostic_b"), access
                )
            finally:
                audit.RAW_ROOT = original_raw_root
            self.assertEqual(set(loaded), {"diagnostic_a", "diagnostic_b"})
            self.assertEqual(loaded_summary, summary)
            self.assertEqual(
                access["G6_DT0"]["loaded_member_names"],
                ["field_0001", "field_0002"],
            )
            self.assertIn("observable_0001", access["G6_DT0"]["enumerated_members"])
            self.assertFalse(access["G6_DT0"]["prohibited_member_values_loaded"])

    def test_failed_archive_loads_only_explicit_mapping_values(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            root = Path(directory) / "runs"
            case_root = root / "cases" / audit.FAILED_CASE
            case_root.mkdir(parents=True)
            summary = {
                "case_id": audit.FAILED_CASE,
                "status": "D3_INCONCLUSIVE_FAIL_CLOSED",
                "failure": {
                    "message": "G9_DT2 hard gate failed: projected_divergence",
                    "accepted_steps": 6695,
                },
            }
            (case_root / "case_summary.json").write_text(
                json.dumps(summary), encoding="utf-8"
            )
            np.savez(
                case_root / "partial.npz",
                allowed_member=np.asarray([1.0, 2.0], dtype=np.float64),
                state_secret=np.asarray([object()], dtype=object),
            )
            original_raw_root = audit.RAW_ROOT
            try:
                audit.RAW_ROOT = root
                access: dict[str, Any] = {}
                loaded, loaded_summary = audit._load_failed_case(
                    {"diagnostic_a": "allowed_member"}, access
                )
            finally:
                audit.RAW_ROOT = original_raw_root
            self.assertEqual(set(loaded), {"diagnostic_a"})
            self.assertEqual(loaded_summary, summary)
            self.assertEqual(
                access[audit.FAILED_CASE]["loaded_member_names"],
                ["allowed_member"],
            )
            self.assertIn(
                "state_secret", access[audit.FAILED_CASE]["enumerated_members"]
            )
            self.assertFalse(
                access[audit.FAILED_CASE]["prohibited_member_values_loaded"]
            )


if __name__ == "__main__":
    unittest.main()
