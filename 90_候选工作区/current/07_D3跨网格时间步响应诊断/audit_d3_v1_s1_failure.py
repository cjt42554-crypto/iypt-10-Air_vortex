#!/usr/bin/env python3
"""Freeze the local D3 V1 S1 fail-closed evidence without reading observables.

This audit is intentionally independent of the D3 runner and analyser.  It
verifies the five Pro-frozen raw-file hashes, reloads only the registered JSON
semantics, and opens ``partial.npz`` solely to enumerate members and read
``frame_time_s``.  No physical observable array is accessed or reported.
"""

from __future__ import annotations

import hashlib
import json
import os
import struct
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

HERE = Path(__file__).resolve().parent
REPOSITORY_ROOT = HERE.parents[2]
RAW_ROOT = HERE / "artifacts" / "runs"
OUTPUT_ROOT = HERE / "artifacts" / "v1_failure"
OUTPUT_JSON = OUTPUT_ROOT / "d3_v1_s1_failure_forensic.json"
OUTPUT_MD = OUTPUT_ROOT / "d3_v1_s1_failure_forensic.md"
PREREGISTRATION_PATH = HERE / "d3_cross_grid_temporal_preregistration.json"

PROTOCOL_ID = "D3_HF3D_CROSS_GRID_TEMPORAL_RESPONSE_V1"
EXECUTION_COMMIT = "90ed5c1019d8265f6e9730ff8a2d36be61deec8b"
PRO_RULING_COMMIT = "b3dcc73a8d57290d372f7d8c41dbd721a01dccaf"
PREREGISTRATION_SHA256 = (
    "74156365c8b5f2ddde4a3ad4d1033eed2ac0fa0ecf69d04b6caa4129b7420620"
)
EXPECTED_TIMES = np.asarray(
    [0.0, 0.001, 0.002, 0.003, 0.004, 0.005, 0.006, 0.007, 0.008, 0.009, 0.01],
    dtype=np.float64,
)

RAW_FILE_HASHES = {
    "s0": (
        RAW_ROOT / "s0_identity_config_fresh_evidence_gate.json",
        "e50905260c9d7732ef68e2cf76b4ed661b19383b4f92ade409463e58b63244b2",
    ),
    "stage_ledger": (
        RAW_ROOT / "stage_ledger.json",
        "46ff32a79a62dd6cb544a04a7377d55888dff75e313f650bd97c32cf2df62afa",
    ),
    "failure": (
        RAW_ROOT / "execution_failure.json",
        "d63bd745ee455bfaaba563b6b348bb7f7c822d5f6d33977c6f0f02daadb7def5",
    ),
    "case_summary": (
        RAW_ROOT / "cases" / "G6_DT0" / "case_summary.json",
        "15b2e76a57ad00dd93eb7a6652f4adf697f48c9dc772f57f666a771a537ca27a",
    ),
    "partial_npz": (
        RAW_ROOT / "cases" / "G6_DT0" / "partial.npz",
        "d1b5ae2723ec60619d20fe2ce7d8d8010941083167fe1d71554990b898d9f3ed",
    ),
}

EXPECTED_STAGE_STATE = {
    "S0": ("EXECUTED", "PASS"),
    "S1": ("FAILED_STOPPED", "D3_INCONCLUSIVE_FAIL_CLOSED"),
    "S2": ("NOT_EXECUTED", "NOT_EXECUTED"),
    "S3": ("NOT_EXECUTED", "NOT_EXECUTED"),
}

ABSENT_PATHS = (
    RAW_ROOT / "cases" / "G6_DT1",
    RAW_ROOT / "cases" / "G6_DT2",
    RAW_ROOT / "cases" / "G9_DT0",
    RAW_ROOT / "cases" / "G9_DT1",
    RAW_ROOT / "cases" / "G9_DT2",
    HERE / "reports" / "d3_cross_grid_temporal_report.json",
    HERE / "reports" / "d3_cross_grid_temporal_report.md",
    HERE / "artifacts" / "d3_cross_grid_temporal_manifest.json",
    HERE / "artifacts" / "evidence",
    RAW_ROOT / ".s3_publication_transaction",
    RAW_ROOT / ".stage_ledger.s3-commit.tmp",
)


class ForensicAuditError(RuntimeError):
    """Raised before bundle publication when frozen evidence does not match."""


def _relative(path: Path) -> str:
    return path.resolve().relative_to(REPOSITORY_ROOT.resolve()).as_posix()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant is prohibited: {value}")


def _object_no_duplicates(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _json_object(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise ForensicAuditError(f"not a regular file: {_relative(path)}")
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_object_no_duplicates,
            parse_constant=_reject_constant,
        )
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise ForensicAuditError(
            f"strict JSON reload failed: {_relative(path)}"
        ) from exc
    if not isinstance(value, dict):
        raise ForensicAuditError(f"top-level JSON object required: {_relative(path)}")
    return value


def _uint64(value: float | np.float64) -> int:
    return struct.unpack(">Q", struct.pack(">d", float(value)))[0]


def _time_identity(
    observed: NDArray[np.float64], registered: NDArray[np.float64]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, (actual, expected) in enumerate(zip(observed, registered, strict=True)):
        actual_bits = _uint64(actual)
        expected_bits = _uint64(expected)
        rows.append(
            {
                "index": index,
                "observed": {
                    "decimal": repr(float(actual)),
                    "float_hex": float(actual).hex(),
                    "uint64": actual_bits,
                },
                "registered": {
                    "decimal": repr(float(expected)),
                    "float_hex": float(expected).hex(),
                    "uint64": expected_bits,
                },
                "ulp_distance": abs(actual_bits - expected_bits),
                "bitwise_equal": actual_bits == expected_bits,
            }
        )
    return rows


def _require(gates: dict[str, bool], name: str, condition: object) -> None:
    passed = bool(condition)
    gates[name] = passed
    if not passed:
        raise ForensicAuditError(f"forensic gate failed: {name}")


def _stage_map(ledger: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    stages = ledger.get("stages")
    if not isinstance(stages, list):
        raise ForensicAuditError("stage ledger does not contain a stage list")
    result: dict[str, Mapping[str, Any]] = {}
    for item in stages:
        if not isinstance(item, Mapping) or not isinstance(item.get("stage"), str):
            raise ForensicAuditError("malformed stage ledger entry")
        stage = str(item["stage"])
        if stage in result:
            raise ForensicAuditError(f"duplicate stage ledger entry: {stage}")
        result[stage] = item
    return result


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# D3 V1 S1 失败取证包",
        "",
        "状态：`D3_V1_S1_FAILURE_FORENSIC_GATE_PASS`",
        "",
        "本取证包只冻结 V1 失败身份与证据边界。它不读取或报告主要物理观测量，不能支持 D3 科学分类。",
        "",
        "## 绑定",
        "",
        f"- 协议：`{report['protocol_id']}`",
        f"- 执行提交：`{report['execution_commit']}`",
        f"- Pro 裁决提交：`{report['pro_ruling_commit']}`",
        f"- 审计脚本 SHA-256：`{report['audit_script_sha256']}`",
        "",
        "## 冻结文件",
        "",
        "| 角色 | 路径 | SHA-256 | 字节数 |",
        "|---|---|---|---:|",
    ]
    for item in report["raw_files"]:
        lines.append(
            f"| {item['role']} | `{item['path']}` | `{item['sha256']}` | {item['size_bytes']} |"
        )
    lines.extend(
        [
            "",
            "## 阶段状态",
            "",
            "| 阶段 | disposition | status |",
            "|---|---|---|",
        ]
    )
    for stage in ("S0", "S1", "S2", "S3"):
        item = report["stage_state"][stage]
        lines.append(f"| {stage} | `{item['disposition']}` | `{item['status']}` |")
    lines.extend(
        [
            "",
            "`subsequent_stages_executed = false`，V1 G6_DT0 仍为硬门失败，不得分类或复用。",
            "",
            "## 原生输出时刻位级身份",
            "",
            "`partial.npz` 只读取了成员清单和 `frame_time_s`；其他数组均未访问。",
            "",
            "| 索引 | observed decimal | registered decimal | ULP | 相等 |",
            "|---:|---:|---:|---:|---|",
        ]
    )
    for row in report["frame_time_identity"]:
        observed = row["observed"]
        registered = row["registered"]
        lines.append(
            f"| {row['index']} | {observed['decimal']} | {registered['decimal']} | "
            f"{row['ulp_distance']} | {str(row['bitwise_equal']).lower()} |"
        )
    lines.extend(
        [
            "",
            "### Observed 位模式",
            "",
            "| 索引 | float.hex | uint64 |",
            "|---:|---|---:|",
        ]
    )
    for row in report["frame_time_identity"]:
        observed = row["observed"]
        lines.append(
            f"| {row['index']} | `{observed['float_hex']}` | {observed['uint64']} |"
        )
    lines.extend(
        [
            "",
            "### Registered 位模式",
            "",
            "| 索引 | float.hex | uint64 |",
            "|---:|---|---:|",
        ]
    )
    for row in report["frame_time_identity"]:
        registered = row["registered"]
        lines.append(
            f"| {row['index']} | `{registered['float_hex']}` | {registered['uint64']} |"
        )
    lines.extend(
        [
            "",
            "仅索引 9 相差 1 ULP；索引 0--8 和 10 逐位相同。",
            "",
            "## 缺失的后续证据",
            "",
        ]
    )
    for path in report["confirmed_absent_paths"]:
        lines.append(f"- `{path}`")
    lines.extend(
        [
            "",
            "## 结论边界",
            "",
            "```text",
            "D3_V1_S1_FAIL_CLOSED = UPHELD",
            "D3_V1_FAILURE_CAUSE = CANDIDATE_OUTPUT_SCHEDULE_FLOAT_ACCUMULATION_DEFECT",
            "D3_V1_SCIENTIFIC_CLASSIFICATION = NOT_FORMED",
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def audit() -> dict[str, Any]:
    gates: dict[str, bool] = {}

    expected_output_files = {OUTPUT_JSON.name, OUTPUT_MD.name}
    if OUTPUT_ROOT.exists():
        _require(
            gates,
            "forensic_output_root_contains_only_registered_files",
            OUTPUT_ROOT.is_dir()
            and not OUTPUT_ROOT.is_symlink()
            and {path.name for path in OUTPUT_ROOT.iterdir()} <= expected_output_files,
        )
    else:
        gates["forensic_output_root_contains_only_registered_files"] = True

    raw_hashes_before: dict[str, str] = {}
    raw_file_records: list[dict[str, Any]] = []
    for role, (path, expected_hash) in RAW_FILE_HASHES.items():
        _require(
            gates, f"{role}_regular_file", path.is_file() and not path.is_symlink()
        )
        actual_hash = _sha256(path)
        raw_hashes_before[role] = actual_hash
        _require(gates, f"{role}_sha256_exact", actual_hash == expected_hash)
        raw_file_records.append(
            {
                "role": role,
                "path": _relative(path),
                "sha256": actual_hash,
                "size_bytes": path.stat().st_size,
            }
        )

    _require(
        gates,
        "v1_preregistration_sha256_exact",
        _sha256(PREREGISTRATION_PATH) == PREREGISTRATION_SHA256,
    )
    preregistration = _json_object(PREREGISTRATION_PATH)
    registered_times = np.asarray(
        preregistration.get("native_output_times_s"), dtype=np.float64
    )
    _require(
        gates, "v1_protocol_id_exact", preregistration.get("protocol_id") == PROTOCOL_ID
    )
    _require(
        gates,
        "v1_registered_times_shape_exact",
        registered_times.shape == EXPECTED_TIMES.shape,
    )
    _require(
        gates,
        "v1_registered_times_bitwise_exact",
        np.array_equal(
            registered_times.view(np.uint64), EXPECTED_TIMES.view(np.uint64)
        ),
    )

    s0 = _json_object(RAW_FILE_HASHES["s0"][0])
    ledger = _json_object(RAW_FILE_HASHES["stage_ledger"][0])
    failure = _json_object(RAW_FILE_HASHES["failure"][0])
    case_summary = _json_object(RAW_FILE_HASHES["case_summary"][0])

    for name, payload in (
        ("s0", s0),
        ("stage_ledger", ledger),
        ("failure", failure),
        ("case_summary", case_summary),
    ):
        _require(
            gates,
            f"{name}_protocol_id_exact",
            payload.get("protocol_id") == PROTOCOL_ID,
        )
        _require(
            gates,
            f"{name}_execution_commit_exact",
            payload.get("execution_commit") == EXECUTION_COMMIT,
        )

    _require(gates, "s0_stage_exact", s0.get("stage") == "S0")
    _require(gates, "s0_gate_pass", s0.get("gate_pass") is True)
    _require(
        gates,
        "s0_status_exact",
        s0.get("status")
        == "D3_S0_IDENTITY_CONFIG_IMPLEMENTATION_AND_FRESH_EVIDENCE_GATE_PASS",
    )

    stages = _stage_map(ledger)
    _require(gates, "stage_set_exact", set(stages) == set(EXPECTED_STAGE_STATE))
    stage_state: dict[str, dict[str, str]] = {}
    for stage, (expected_disposition, expected_status) in EXPECTED_STAGE_STATE.items():
        item = stages[stage]
        disposition = item.get("disposition")
        status = item.get("status")
        _require(
            gates,
            f"{stage.lower()}_disposition_exact",
            disposition == expected_disposition,
        )
        _require(gates, f"{stage.lower()}_status_exact", status == expected_status)
        stage_state[stage] = {
            "disposition": str(disposition),
            "status": str(status),
        }
    _require(gates, "ledger_failed_stages_exact", ledger.get("failed_stages") == ["S1"])
    _require(
        gates,
        "ledger_unexecuted_stages_exact",
        ledger.get("unexecuted_stages") == ["S2", "S3"],
    )
    _require(
        gates,
        "ledger_terminal_state_exact",
        ledger.get("terminal_state") == "D3_INCONCLUSIVE_FAIL_CLOSED",
    )
    _require(
        gates,
        "ledger_automatic_follow_on_prohibited",
        ledger.get("automatic_follow_on") == "PROHIBITED",
    )
    _require(
        gates,
        "ledger_failure_record_exact",
        ledger.get("failure_record") == _relative(RAW_FILE_HASHES["failure"][0]),
    )

    _require(gates, "failure_stage_exact", failure.get("stage") == "S1")
    _require(
        gates,
        "failure_status_exact",
        failure.get("status") == "D3_INCONCLUSIVE_FAIL_CLOSED",
    )
    _require(
        gates,
        "subsequent_stages_not_executed",
        failure.get("subsequent_stages_executed") is False,
    )
    failure_detail = failure.get("failure")
    _require(
        gates,
        "failure_type_exact",
        isinstance(failure_detail, Mapping)
        and failure_detail.get("type") == "D3ExecutionError",
    )
    _require(
        gates,
        "failure_command_exact",
        failure.get("command") == ["run-stage", "--stage", "S1"],
    )

    _require(gates, "case_id_exact", case_summary.get("case_id") == "G6_DT0")
    _require(gates, "case_stage_exact", case_summary.get("stage") == "S1")
    _require(
        gates,
        "case_status_exact",
        case_summary.get("status") == "D3_INCONCLUSIVE_FAIL_CLOSED",
    )
    _require(gates, "case_gate_failed", case_summary.get("gate_pass") is False)
    case_gates = case_summary.get("gates")
    quality = case_summary.get("quality")
    quality_gates = quality.get("gates") if isinstance(quality, Mapping) else None
    _require(
        gates,
        "case_completed_before_quality_failure",
        isinstance(case_gates, Mapping) and case_gates.get("run_completed") is True,
    )
    _require(
        gates,
        "case_quality_gate_failed",
        isinstance(quality, Mapping) and quality.get("gate_pass") is False,
    )
    _require(
        gates,
        "native_output_times_exact_is_only_failed_quality_gate",
        isinstance(quality_gates, Mapping)
        and quality_gates.get("native_output_times_exact") is False
        and all(
            value is True
            for key, value in quality_gates.items()
            if key != "native_output_times_exact"
        ),
    )
    partial_evidence = case_summary.get("partial_evidence")
    _require(
        gates,
        "case_partial_path_exact",
        isinstance(partial_evidence, Mapping)
        and partial_evidence.get("path")
        == _relative(RAW_FILE_HASHES["partial_npz"][0]),
    )
    _require(
        gates,
        "case_partial_sha256_exact",
        isinstance(partial_evidence, Mapping)
        and partial_evidence.get("sha256") == RAW_FILE_HASHES["partial_npz"][1],
    )

    partial_path = RAW_FILE_HASHES["partial_npz"][0]
    with np.load(partial_path, allow_pickle=False) as archive:
        member_names = list(archive.files)
        _require(
            gates,
            "partial_member_names_unique",
            len(member_names) == len(set(member_names)),
        )
        _require(
            gates,
            "frame_time_member_present_once",
            member_names.count("frame_time_s") == 1,
        )
        frame_time = np.asarray(archive["frame_time_s"], dtype=np.float64).copy()
    _require(
        gates, "frame_time_shape_exact", frame_time.shape == registered_times.shape
    )
    _require(gates, "frame_time_all_finite", np.isfinite(frame_time).all())
    time_rows = _time_identity(frame_time, registered_times)
    mismatch_indices = [row["index"] for row in time_rows if not row["bitwise_equal"]]
    _require(gates, "only_index_9_differs", mismatch_indices == [9])
    _require(gates, "index_9_ulp_distance_one", time_rows[9]["ulp_distance"] == 1)
    _require(
        gates,
        "index_9_observed_hex_exact",
        time_rows[9]["observed"]["float_hex"] == "0x1.26e978d4fdf3cp-7",
    )
    _require(
        gates,
        "index_9_registered_hex_exact",
        time_rows[9]["registered"]["float_hex"] == "0x1.26e978d4fdf3bp-7",
    )

    _require(
        gates,
        "only_g6_dt0_case_directory_present",
        (RAW_ROOT / "cases").is_dir()
        and not (RAW_ROOT / "cases").is_symlink()
        and sorted(
            path.name for path in (RAW_ROOT / "cases").iterdir() if path.is_dir()
        )
        == ["G6_DT0"],
    )
    for path in ABSENT_PATHS:
        _require(gates, f"absent::{_relative(path)}", not path.exists())

    raw_hashes_after = {
        role: _sha256(path) for role, (path, _expected) in RAW_FILE_HASHES.items()
    }
    _require(
        gates,
        "raw_hashes_unchanged_during_audit",
        raw_hashes_after == raw_hashes_before,
    )

    report: dict[str, Any] = {
        "schema_version": 1,
        "audit_id": "D3_V1_S1_FAILURE_FORENSIC_V1",
        "status": "D3_V1_S1_FAILURE_FORENSIC_GATE_PASS",
        "gate_pass": True,
        "protocol_id": PROTOCOL_ID,
        "execution_commit": EXECUTION_COMMIT,
        "pro_ruling_commit": PRO_RULING_COMMIT,
        "audit_script_sha256": _sha256(Path(__file__).resolve()),
        "scope": {
            "purpose": "freeze V1 S1 fail-closed evidence before D3R1 implementation",
            "partial_npz_arrays_read": ["frame_time_s"],
            "partial_npz_other_arrays_read": False,
            "physical_observables_reported": False,
            "scientific_classification_supported": False,
        },
        "raw_files": raw_file_records,
        "raw_hashes_unchanged_during_audit": True,
        "stage_state": stage_state,
        "subsequent_stages_executed": False,
        "partial_npz": {
            "path": _relative(partial_path),
            "member_names": member_names,
            "member_count": len(member_names),
            "arrays_read": ["frame_time_s"],
        },
        "frame_time_identity": time_rows,
        "mismatch_indices": mismatch_indices,
        "confirmed_absent_paths": [_relative(path) for path in ABSENT_PATHS],
        "gates": gates,
        "conclusion": {
            "v1_failure": "UPHELD",
            "failure_cause": "CANDIDATE_OUTPUT_SCHEDULE_FLOAT_ACCUMULATION_DEFECT",
            "scientific_classification": "NOT_FORMED",
            "g6_dt0_reuse": "PROHIBITED",
        },
    }
    return report


def main() -> int:
    report = audit()
    json_text = (
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    )
    markdown_text = _markdown(report)
    _write_atomic(OUTPUT_JSON, json_text)
    _write_atomic(OUTPUT_MD, markdown_text)
    print(
        json.dumps(
            {
                "status": report["status"],
                "gate_pass": report["gate_pass"],
                "json": _relative(OUTPUT_JSON),
                "markdown": _relative(OUTPUT_MD),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
