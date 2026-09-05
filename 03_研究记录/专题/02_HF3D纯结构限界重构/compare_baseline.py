#!/usr/bin/env python3
"""Fail-closed structural and elementwise comparator for HF3D captures."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np

from capture_baseline import FORMAT_VERSION, array_sha256


ALLOWED_DIFFERENCES = (
    "capture_label",
    "array_archive",
    "provenance.git_head",
    "provenance.source_tree",
)


def _first_json_difference(
    before: object,
    after: object,
    path: str,
) -> dict[str, object] | None:
    if type(before) is not type(after):
        return {
            "path": path,
            "kind": "json_type",
            "before": type(before).__name__,
            "after": type(after).__name__,
        }
    if isinstance(before, dict):
        before_keys = list(before)
        after_keys = list(after)
        if before_keys != after_keys:
            return {
                "path": path,
                "kind": "json_keys_or_order",
                "before": before_keys,
                "after": after_keys,
            }
        for key in before_keys:
            difference = _first_json_difference(
                before[key], after[key], f"{path}.{key}"
            )
            if difference is not None:
                return difference
        return None
    if isinstance(before, list):
        if len(before) != len(after):
            return {
                "path": path,
                "kind": "json_length",
                "before": len(before),
                "after": len(after),
            }
        for index, (left, right) in enumerate(zip(before, after)):
            difference = _first_json_difference(
                left, right, f"{path}[{index}]"
            )
            if difference is not None:
                return difference
        return None
    if before != after:
        return {
            "path": path,
            "kind": "json_value",
            "before": before,
            "after": after,
        }
    return None


def _load_manifest(path: Path) -> dict[str, object]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"capture manifest must be an object: {path}")
    if raw.get("format_version") != FORMAT_VERSION:
        raise ValueError(f"unsupported capture format: {path}")
    return raw


def _comparable_manifest(manifest: dict[str, object]) -> dict[str, object]:
    comparable = {
        key: value
        for key, value in manifest.items()
        if key not in {"capture_label", "array_archive", "provenance", "arrays"}
    }
    return comparable


def _array_index(manifest: dict[str, object]) -> dict[str, dict[str, object]]:
    records = manifest.get("arrays")
    if not isinstance(records, list):
        raise ValueError("capture arrays must be a list")
    index: dict[str, dict[str, object]] = {}
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("storage_key"), str):
            raise ValueError("invalid capture array record")
        key = record["storage_key"]
        if key in index:
            raise ValueError(f"duplicate capture array key: {key}")
        index[key] = record
    return index


def _load_arrays(
    manifest_path: Path,
    manifest: dict[str, object],
) -> tuple[dict[str, np.ndarray], dict[str, dict[str, object]]]:
    archive_name = manifest.get("array_archive")
    if not isinstance(archive_name, str) or not archive_name:
        raise ValueError("capture array_archive must be a filename")
    archive_path = manifest_path.parent / archive_name
    index = _array_index(manifest)
    with np.load(archive_path, allow_pickle=False) as archive:
        if list(archive.files) != list(index):
            raise ValueError(
                f"array archive keys/order differ from manifest: {archive_path}"
            )
        arrays = {name: archive[name].copy() for name in archive.files}
    for name, array in arrays.items():
        record = index[name]
        if array.dtype.str != record.get("dtype"):
            raise ValueError(f"array dtype does not match manifest: {name}")
        if list(array.shape) != record.get("shape"):
            raise ValueError(f"array shape does not match manifest: {name}")
        if array_sha256(array) != record.get("sha256"):
            raise ValueError(f"array hash does not match manifest: {name}")
    return arrays, index


def compare_captures(before_path: Path, after_path: Path) -> dict[str, object]:
    try:
        before_manifest = _load_manifest(before_path)
        after_manifest = _load_manifest(after_path)
        json_difference = _first_json_difference(
            _comparable_manifest(before_manifest),
            _comparable_manifest(after_manifest),
            "capture",
        )
        if json_difference is not None:
            return {
                "status": "FAIL",
                "pass": False,
                "first_difference": json_difference,
                "allowed_differences": list(ALLOWED_DIFFERENCES),
                "arrays_compared": 0,
            }
        before_arrays, before_index = _load_arrays(before_path, before_manifest)
        after_arrays, after_index = _load_arrays(after_path, after_manifest)
        if list(before_index) != list(after_index):
            return {
                "status": "FAIL",
                "pass": False,
                "first_difference": {
                    "path": "capture.arrays",
                    "kind": "array_keys_or_order",
                    "before": list(before_index),
                    "after": list(after_index),
                },
                "allowed_differences": list(ALLOWED_DIFFERENCES),
                "arrays_compared": 0,
            }
        for index, name in enumerate(before_index):
            before_record = before_index[name]
            after_record = after_index[name]
            for attribute in ("category", "logical_name", "dtype", "shape"):
                if before_record.get(attribute) != after_record.get(attribute):
                    return {
                        "status": "FAIL",
                        "pass": False,
                        "first_difference": {
                            "path": f"capture.arrays.{name}.{attribute}",
                            "kind": "array_contract",
                            "before": before_record.get(attribute),
                            "after": after_record.get(attribute),
                        },
                        "allowed_differences": list(ALLOWED_DIFFERENCES),
                        "arrays_compared": index,
                    }
            before_array = before_arrays[name]
            after_array = after_arrays[name]
            if not np.array_equal(before_array, after_array):
                differing = np.argwhere(before_array != after_array)
                first_index = tuple(int(item) for item in differing[0])
                return {
                    "status": "FAIL",
                    "pass": False,
                    "first_difference": {
                        "path": f"capture.arrays.{name}{first_index}",
                        "kind": "array_value",
                        "before": float(before_array[first_index]),
                        "after": float(after_array[first_index]),
                    },
                    "allowed_differences": list(ALLOWED_DIFFERENCES),
                    "arrays_compared": index,
                }
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {
            "status": "FAIL",
            "pass": False,
            "first_difference": {
                "path": "capture",
                "kind": "unreadable_or_invalid_evidence",
                "detail": f"{type(exc).__name__}: {exc}",
            },
            "allowed_differences": list(ALLOWED_DIFFERENCES),
            "arrays_compared": 0,
        }
    return {
        "status": "PASS_STRICT_ELEMENTWISE_EQUIVALENCE",
        "pass": True,
        "first_difference": None,
        "allowed_differences": list(ALLOWED_DIFFERENCES),
        "arrays_compared": len(before_index),
        "requested_device": before_manifest["requested_device"],
        "before_git_head": before_manifest["provenance"]["git_head"],
        "after_git_head": after_manifest["provenance"]["git_head"],
        "before_source_tree_sha256": before_manifest["provenance"]["source_tree"][
            "value"
        ],
        "after_source_tree_sha256": after_manifest["provenance"]["source_tree"][
            "value"
        ],
    }


def _write_report(path: Path, report: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _write_markdown(path: Path, report: dict[str, object]) -> None:
    lines = [
        "# HF3D 重构严格等价性报告",
        "",
        f"结论：`{report['status']}`。",
        "",
        f"逐元素完成比较的数组数量：`{report['arrays_compared']}`。",
        "",
        "允许差异白名单：",
        "",
    ]
    lines.extend(f"- `{item}`" for item in report["allowed_differences"])
    if report["first_difference"] is not None:
        lines.extend(
            [
                "",
                "首个差异：",
                "",
                "```json",
                json.dumps(
                    report["first_difference"],
                    ensure_ascii=False,
                    allow_nan=False,
                    sort_keys=True,
                    indent=2,
                ),
                "```",
            ]
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("before", type=Path)
    parser.add_argument("after", type=Path)
    parser.add_argument("--report-json", type=Path)
    parser.add_argument("--report-md", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = compare_captures(args.before.resolve(), args.after.resolve())
    if args.report_json is not None:
        _write_report(args.report_json.resolve(), report)
    if args.report_md is not None:
        _write_markdown(args.report_md.resolve(), report)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
